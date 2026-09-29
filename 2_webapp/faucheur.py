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
import subprocess  # nosec B404 - le sujet impose de piloter Docker par la CLI
import sys
import threading

import database as db
import services
import workers as wk
from docker_ops import MACHINE_FILTERS
from config import AUTO_RECOVERY

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
def _docker(*args, worker: str = "local") -> subprocess.CompletedProcess:
    """Exécute `docker <args>` sur le nœud `worker` sans lever d'exception (code retour vérifié par l'appelant)."""
    try:
        # Appel sans shell, arguments en liste : aucune interpolation possible.
        return subprocess.run(  # nosec B603  # noqa: S603
            [*wk.docker_command(worker), *args],
            capture_output=True, text=True, timeout=DOCKER_TIMEOUT,
        )
    except FileNotFoundError:
        log.error("La commande 'docker' est introuvable sur cette machine.")
    except subprocess.TimeoutExpired:
        log.error("Docker n'a pas répondu à temps pour : docker %s", " ".join(args))
    # Objet factice avec un code d'erreur pour uniformiser le traitement
    return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="docker indisponible")


def remove_container(container_id: str, worker: str = "local") -> bool:
    """
    Supprime un conteneur de force (`docker rm -f`) sur son nœud.
    Retourne True si le conteneur est absent après l'opération
    (supprimé maintenant, ou déjà inexistant).
    """
    # Journalisée systématiquement : c'est la seule trace qu'une machine a
    # été détruite, et elle doit pouvoir être retrouvée après coup.
    log.info("Destruction du conteneur %s sur %s", container_id, worker)
    res = _docker("rm", "-f", container_id, worker=worker)
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
            "Instance #%s expirée (user=%s, conteneur=%s, nœud=%s, port=%s) -> destruction",
            inst["id"], inst["username"], inst["container_name"], inst["worker"], inst["port"],
        )
        if remove_container(inst["container_id"], inst["worker"]):
            db.set_instance_status(inst["id"], db.STATUS_EXPIRED)
            log.info("Instance #%s marquée 'expired'.", inst["id"])
        # Sinon, on réessaiera au prochain cycle.
    return len(expired)


def reconcile() -> None:
    """
    Remet Docker et la BDD en cohérence, nœud par nœud :
      - conteneur insacloud_* sans instance 'running' en BDD  -> docker rm -f
      - instance 'running' en BDD sans conteneur dans Docker  -> status 'stopped'
    Un nœud injoignable est simplement ignoré (rien n'est modifié pour lui).
    """
    all_active = db.get_active_instances()
    for worker in wk.WORKERS:
        # Filtre par LABEL, jamais par nom : les conteneurs de la plateforme
        # (insacloud-web, insacloud-db, insacloud-faucheur) ne doivent jamais
        # être pris pour des machines louées et détruits comme orphelins.
        filtres = []
        for filtre in MACHINE_FILTERS:
            filtres += ["--filter", filtre]
        res = _docker("ps", "-a", *filtres,
                      "--format", "{{.ID}} {{.Names}}", worker=worker)
        if res.returncode != 0:
            log.warning("Nœud %s injoignable pour la réconciliation : %s", worker, res.stderr.strip()[:120])
            continue

        present = {}
        for line in res.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) == 2:
                present[parts[0][:12]] = parts[1]
        active = {inst["container_id"][:12]: inst for inst in all_active if inst["worker"] == worker}

        for cid, name in present.items():
            if cid not in active:
                log.warning("[%s] Conteneur orphelin détecté : %s (%s) -> suppression", worker, name, cid)
                remove_container(cid, worker)
        for cid, inst in active.items():
            if cid not in present:
                log.warning("[%s] Instance #%s (%s) n'a plus de conteneur -> statut 'stopped'",
                            worker, inst["id"], inst["container_name"])
                db.set_instance_status(inst["id"], db.STATUS_STOPPED)


def battre_pour_le_noeud_local() -> None:
    """
    En mode mono-hôte, le Faucheur tient lieu d'agent pour le nœud « local ».

    Sans cela, ce nœud fictif — qui n'héberge aucun agent puisqu'il EST le
    contrôleur — finirait par expirer faute de battement de cœur : ses machines
    seraient déclarées arrêtées, puis supprimées comme orphelines à la
    réconciliation suivante. Le battement n'est envoyé que si le démon Docker
    local répond vraiment : c'est une vérification, pas une formalité.
    """
    if wk.WORKERS != {db.LOCAL_WORKER: None}:
        return                                  # de vrais workers, avec leurs agents
    if db.get_worker_by_hostname(db.LOCAL_WORKER) is None:
        return                                  # aucune machine louée pour l'instant
    res = _docker("info", "--format", "{{.ServerVersion}}", worker=db.LOCAL_WORKER)
    if res.returncode == 0:
        db.worker_heartbeat(db.LOCAL_WORKER)
    else:
        log.error("Le démon Docker local ne répond pas : %s", res.stderr.strip()[:120])


def surveiller_workers() -> int:
    """
    Détecte les workers muets et remet leurs machines en service ailleurs.

    Le mécanisme complet, tel que le cahier des charges le décrit :
        worker1 en panne -> OFFLINE -> recherche d'un worker disponible
        -> recréation du conteneur sur worker2 -> instance mise à jour

    Retourne le nombre d'instances effectivement reprises.
    """
    bascules = db.mark_stale_workers_offline()
    if not bascules:
        return 0

    reprises = 0
    for hostname in bascules:
        instances = db.get_instances_on_worker(hostname, actives_seulement=True)
        log.error("Worker %s ne répond plus (aucun battement de cœur) : "
                  "%d machine(s) à reprendre.", hostname, len(instances))
        if not instances:
            continue
        if not AUTO_RECOVERY:
            log.warning("Reprise automatique désactivée : les machines de %s "
                        "restent marquées actives.", hostname)
            continue
        for nom, resultat in services.reprendre_worker(hostname):
            if resultat.ok:
                reprises += 1
                log.warning("Machine %s reprise : %s", nom, resultat.message)
            else:
                log.error("Reprise impossible pour %s : %s", nom, resultat.message)
    return reprises


def run_forever() -> None:
    """Boucle principale : tourne jusqu'à réception d'un signal d'arrêt."""
    log.info("Faucheur démarré (intervalle = %ss, base = %s, reprise auto = %s)",
             INTERVAL, db.url_sans_secret(), AUTO_RECOVERY)
    db.init_db()  # s'assure que le schéma existe même si Flask n'a pas encore tourné
    cycle = 0
    while not _stop_event.is_set():
        cycle += 1
        try:
            n = reap_expired()
            if n:
                log.info("%d instance(s) fauchée(s) ce cycle.", n)
            # La surveillance du parc tourne à chaque cycle : une panne de
            # worker doit être vue en quelques secondes, pas à la prochaine
            # réconciliation.
            battre_pour_le_noeud_local()
            reprises = surveiller_workers()
            if reprises:
                log.warning("%d machine(s) reprises sur un autre nœud.", reprises)
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
