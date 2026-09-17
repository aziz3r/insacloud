# -*- coding: utf-8 -*-
"""
faucheur.py - Le Faucheur : démon de nettoyage des machines expirées.

Rôle :
  1. Lire la base SQLite en boucle (toutes les INTERVAL secondes).
  2. Détecter les instances dont `expires_at` est dépassée.
  3. Exécuter `docker rm -f <id_conteneur>` pour chacune.
  4. Mettre à jour la BDD (status = 'expired').

Bonus (robustesse) : une passe de "réconciliation" périodique
  - supprime les conteneurs `insacloud_*` orphelins (présents dans Docker
    mais inconnus de la BDD, par ex. après un crash de Flask) ;
  - marque comme 'stopped' les instances dont le conteneur a disparu
    (supprimé à la main par un administrateur).

Deux modes d'exécution :
  - Processus autonome (recommandé, c'est ce que fait le service systemd) :
        python3 faucheur.py
  - Thread en arrière-plan de Flask (pratique en développement) :
        import faucheur ; faucheur.start_in_background()
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time

import database as db

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
INTERVAL = int(os.environ.get("INSACLOUD_FAUCHEUR_INTERVAL", "10"))  # secondes
CONTAINER_PREFIX = "insacloud_"        # préfixe des conteneurs gérés par la plateforme
RECONCILE_EVERY = 6                    # passe de réconciliation tous les N cycles (~1 min)
DOCKER_TIMEOUT = 60                    # secondes max pour une commande docker

log = logging.getLogger("faucheur")

# Événement partagé pour demander l'arrêt propre de la boucle
_stop_event = threading.Event()


# -----------------------------------------------------------------------------
# Wrapper Docker
# -----------------------------------------------------------------------------
def _docker(*args) -> subprocess.CompletedProcess:
    """Exécute `docker <args>` sans lever d'exception (le code retour est vérifié par l'appelant)."""
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True, text=True, timeout=DOCKER_TIMEOUT,
        )
    except FileNotFoundError:
        log.error("La commande 'docker' est introuvable sur cette machine.")
    except subprocess.TimeoutExpired:
        log.error("Docker n'a pas répondu à temps pour : docker %s", " ".join(args))
    # Objet factice avec un code d'erreur pour uniformiser le traitement
    return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="docker indisponible")


def remove_container(container_id: str) -> bool:
    """
    Supprime un conteneur de force (`docker rm -f`).
    Retourne True si le conteneur est absent après l'opération
    (supprimé maintenant, ou déjà inexistant).
    """
    res = _docker("rm", "-f", container_id)
    if res.returncode == 0:
        return True
    if "No such container" in res.stderr:
        log.warning("Conteneur %s déjà absent, on considère la suppression faite.", container_id)
        return True
    log.error("Échec de 'docker rm -f %s' : %s", container_id, res.stderr.strip())
    return False


# -----------------------------------------------------------------------------
# Cœur du Faucheur
# -----------------------------------------------------------------------------
def reap_expired() -> int:
    """
    Détruit toutes les instances expirées et met la BDD à jour.
    Retourne le nombre d'instances traitées.
    """
    expired = db.get_expired_instances()
    for inst in expired:
        log.info(
            "Instance #%s expirée (user=%s, conteneur=%s, port=%s) -> destruction",
            inst["id"], inst["username"], inst["container_name"], inst["port"],
        )
        if remove_container(inst["container_id"]):
            db.set_instance_status(inst["id"], db.STATUS_EXPIRED)
            log.info("Instance #%s marquée 'expired'.", inst["id"])
        # Sinon, on réessaiera au prochain cycle.
    return len(expired)


def reconcile() -> None:
    """
    Remet Docker et la BDD en cohérence :
      - conteneur insacloud_* sans instance 'running' en BDD  -> docker rm -f
      - instance 'running' en BDD sans conteneur dans Docker  -> status 'stopped'
    """
    res = _docker("ps", "-a", "--filter", f"name={CONTAINER_PREFIX}",
                  "--format", "{{.ID}} {{.Names}}")
    if res.returncode != 0:
        return  # Docker indisponible : on ne touche à rien

    # Conteneurs réellement présents dans Docker : {id_court: nom}
    present = {}
    for line in res.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            present[parts[0][:12]] = parts[1]

    # Instances que la BDD considère comme actives : {id_court: instance}
    active = {inst["container_id"][:12]: inst for inst in db.get_active_instances()}

    # 1) Orphelins Docker : présents mais inconnus de la BDD
    for cid, name in present.items():
        if cid not in active:
            log.warning("Conteneur orphelin détecté : %s (%s) -> suppression", name, cid)
            remove_container(cid)

    # 2) Fantômes BDD : actifs en BDD mais disparus de Docker
    for cid, inst in active.items():
        if cid not in present:
            log.warning("Instance #%s (%s) n'a plus de conteneur -> statut 'stopped'",
                        inst["id"], inst["container_name"])
            db.set_instance_status(inst["id"], db.STATUS_STOPPED)


def run_forever() -> None:
    """Boucle principale : tourne jusqu'à réception d'un signal d'arrêt."""
    log.info("Faucheur démarré (intervalle = %ss, BDD = %s)", INTERVAL, db.DB_PATH)
    db.init_db()  # s'assure que le schéma existe même si Flask n'a pas encore tourné
    cycle = 0
    while not _stop_event.is_set():
        cycle += 1
        try:
            n = reap_expired()
            if n:
                log.info("%d instance(s) fauchée(s) ce cycle.", n)
            if cycle % RECONCILE_EVERY == 0:
                reconcile()
        except Exception:  # noqa: BLE001 - le démon ne doit jamais mourir
            log.exception("Erreur inattendue dans le cycle du Faucheur")
        # wait() se réveille immédiatement si un arrêt est demandé
        _stop_event.wait(INTERVAL)
    log.info("Faucheur arrêté proprement.")


def stop() -> None:
    """Demande l'arrêt de la boucle (utilisé par les signaux et par Flask)."""
    _stop_event.set()


def start_in_background() -> threading.Thread:
    """
    Lance le Faucheur dans un thread démon (mode "embarqué" dans Flask).
    Le thread meurt automatiquement avec le processus principal.
    """
    thread = threading.Thread(target=run_forever, name="faucheur", daemon=True)
    thread.start()
    return thread


# -----------------------------------------------------------------------------
# Point d'entrée : mode processus autonome (service systemd)
# -----------------------------------------------------------------------------
def _handle_signal(signum, frame):  # noqa: ARG001
    log.info("Signal %s reçu, arrêt demandé…", signum)
    stop()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,  # capté par journald sous systemd
    )
    # SIGTERM est envoyé par `systemctl stop`, SIGINT par Ctrl+C
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run_forever()
