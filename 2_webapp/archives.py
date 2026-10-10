"""
archives.py - Sauvegarde du travail d'une machine louée avant sa destruction.

Principe : un `tar.gz` des dossiers de l'utilisateur (/root et /home par
défaut) est lu dans le conteneur par `docker exec <id> tar czf - ...`, puis
écrit au fil de l'eau dans un fichier sur le contrôleur. Pour un worker
distant, le transport SSH du client Docker (`docker -H ssh://...`) fait
passer le flux : rien de plus n'est exposé sur le réseau.

Ce module ne connaît ni Flask ni la base de données, comme `docker_ops.py` :
il est testable sans serveur et appelable par l'application comme par le
Faucheur.

Règle de conception : `archiver_conteneur` ne lève JAMAIS d'exception. Un
échec d'archivage (nœud injoignable, archive trop lourde, disque plein) doit
être signalé à l'appelant, pas l'empêcher de détruire la machine : sinon une
machine expirée resterait en vie indéfiniment.
"""

import hashlib
import logging
import os
import secrets
import subprocess  # nosec B404 - le flux tar vient de la CLI Docker, comme dans docker_ops
import threading
import time
from pathlib import Path
import workers as wk
from config import ARCHIVE_DIR, ARCHIVE_MAX_BYTES, ARCHIVE_PATHS, ARCHIVE_TIMEOUT

log = logging.getLogger("insacloud.archives")

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_TOO_LARGE = "too_large"

TAILLE_BLOC = 64 * 1024


def _copier_flux(commande: list, destination: Path) -> dict:
    """
    Lance `commande` et écrit sa sortie standard dans `destination`, en
    s'arrêtant dès que la taille maximale ou le délai est dépassé.

    Le délai est surveillé par un minuteur qui tue le processus : une simple
    vérification dans la boucle de lecture ne suffirait pas, car `read()` reste
    bloquant tant que Docker (ou la connexion SSH) n'envoie rien.
    """
    taille = 0
    empreinte = hashlib.sha256()
    delai_depasse = threading.Event()

    # O_EXCL : on ne réécrit jamais un fichier existant. Mode 0600 dès la
    # création : l'archive peut contenir des clés ou des mots de passe.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as sortie, subprocess.Popen(  # nosec B603  # noqa: S603
            commande, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as processus:

        def _delai():
            delai_depasse.set()
            processus.kill()

        minuteur = threading.Timer(ARCHIVE_TIMEOUT, _delai)
        minuteur.start()
        try:
            while bloc := processus.stdout.read(TAILLE_BLOC):
                taille += len(bloc)
                if taille > ARCHIVE_MAX_BYTES:
                    processus.kill()
                    return {"status": STATUS_TOO_LARGE}
                sortie.write(bloc)
                empreinte.update(bloc)
        finally:
            minuteur.cancel()
        processus.wait()

    if delai_depasse.is_set():
        return {"status": STATUS_FAILED, "detail": "délai dépassé"}
    # tar renvoie 1 quand un fichier a changé pendant la lecture : l'archive
    # est alors exploitable (la machine tournait encore). Au-delà, c'est une erreur.
    if processus.returncode not in (0, 1):
        return {"status": STATUS_FAILED, "detail": f"tar a échoué (code {processus.returncode})"}
    return {"status": STATUS_OK, "size": taille, "sha256": empreinte.hexdigest()}


def archiver_conteneur(container_id: str, worker: str, user_id: int, instance_id: int) -> dict:
    """
    Archive le travail d'un conteneur. Ne lève jamais.

    Retourne toujours un dictionnaire avec une clé `status` :
      - "ok"        : + `path` (relatif à ARCHIVE_DIR), `size` (octets), `sha256`
      - "too_large" : l'archive dépasse ARCHIVE_MAX_BYTES, rien n'est conservé
      - "failed"    : nœud injoignable, délai dépassé, disque plein... (+ `detail`)
    """
    # Le nom du fichier vient du serveur (identifiants entiers + jeton
    # aléatoire), jamais d'une saisie utilisateur : pas de traversée de chemin.
    nom = f"{int(instance_id)}-{secrets.token_hex(8)}.tar.gz"
    dossier = Path(ARCHIVE_DIR) / str(int(user_id))
    final = dossier / nom
    partiel = dossier / (nom + ".part")
    commande = [*wk.docker_command(worker), "exec", container_id,
                "tar", "czf", "-", "-C", "/", *ARCHIVE_PATHS]

    log.info("Archivage du conteneur %s (nœud=%s)", container_id, worker)
    try:
        dossier.mkdir(mode=0o700, parents=True, exist_ok=True)
        resultat = _copier_flux(commande, partiel)
        if resultat["status"] == STATUS_OK:
            partiel.rename(final)
            resultat["path"] = f"{int(user_id)}/{nom}"
    except (OSError, subprocess.SubprocessError) as erreur:
        log.error("Archivage impossible pour %s : %s", container_id, erreur)
        resultat = {"status": STATUS_FAILED, "detail": str(erreur)}

    if resultat["status"] != STATUS_OK:
        # Jamais de fichier à moitié écrit : on ne garde que des archives entières.
        partiel.unlink(missing_ok=True)
        log.warning("Archive non conservée pour %s : %s", container_id, resultat)
    return resultat


def supprimer_fichier(chemin_relatif: str) -> bool:
    """
    Efface une archive du disque. Retourne True si elle n'y est plus ensuite
    (effacée maintenant, ou déjà absente) et False si on ne doit pas ou ne peut pas.

    Le chemin vient de la base de données : il est donc revérifié ici, comme au
    téléchargement. Un chemin qui sortirait du dossier des archives (« ../ »,
    lien symbolique) est refusé : cette fonction ne doit jamais pouvoir effacer
    autre chose qu'une archive.
    """
    racine = Path(ARCHIVE_DIR).resolve()
    cible = (racine / chemin_relatif).resolve()
    if cible == racine or not cible.is_relative_to(racine):
        log.error("Refus d'effacer hors du dossier des archives : %s", chemin_relatif)
        return False
    try:
        cible.unlink(missing_ok=True)
    except OSError as erreur:
        log.error("Impossible d'effacer l'archive %s : %s", chemin_relatif, erreur)
        return False
    return True


def nettoyer_partiels(age_max: int = 3600) -> int:
    """
    Efface les fichiers `.part` abandonnés (arrêt brutal pendant un archivage).

    Un `.part` récent est peut-être en cours d'écriture : seuls ceux plus vieux
    que `age_max` secondes sont effacés. Retourne le nombre de fichiers effacés.
    """
    racine = Path(ARCHIVE_DIR)
    if not racine.is_dir():
        return 0
    limite = time.time() - age_max
    effaces = 0
    for partiel in racine.glob("*/*.part"):
        try:
            if partiel.stat().st_mtime < limite:
                partiel.unlink()
                effaces += 1
        except OSError as erreur:
            log.warning("Fichier partiel %s non effacé : %s", partiel, erreur)
    return effaces
