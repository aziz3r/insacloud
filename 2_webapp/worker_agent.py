#!/usr/bin/env python3
"""
worker_agent.py - Agent installé sur chaque nœud d'exécution.

Au démarrage, il se présente au contrôleur (POST /workers/register) en
annonçant son nom, son adresse, ses cœurs, sa mémoire et la présence de
Docker. Ensuite il envoie un battement de cœur régulier (POST
/workers/heartbeat). Le contrôleur n'a donc aucune liste de nœuds à tenir à
jour : un worker qu'on allume rejoint le parc tout seul, un worker qui se tait
en sort.

    Worker -> agent -> POST /workers/register -> contrôleur -> base

Trois états possibles côté contrôleur :
    AVAILABLE  joignable et en dessous de sa capacité
    BUSY       joignable mais plein
    OFFLINE    sans battement de cœur depuis INSACLOUD_HEARTBEAT_TIMEOUT

Cet agent n'utilise QUE la bibliothèque standard : il tourne sur un worker
sans qu'on y installe le moindre paquet Python.

Configuration (variables d'environnement, déposées par Ansible) :
    INSACLOUD_CONTROLLER    https://192.168.56.10   (obligatoire)
    INSACLOUD_AGENT_TOKEN   jeton partagé            (obligatoire)
    INSACLOUD_WORKER_NAME   nom annoncé              (défaut : hostname)
    INSACLOUD_WORKER_IP     adresse annoncée         (défaut : déduite)
    INSACLOUD_HEARTBEAT_INTERVAL  secondes entre deux battements (défaut 30)
    INSACLOUD_AGENT_INSECURE      1 = accepter un certificat auto-signé
"""

import json
import logging
import os
import signal
import socket
import ssl
import subprocess  # nosec B404 - l'agent vérifie Docker par sa ligne de commande
import sys
import threading
import shutil
import urllib.error
import urllib.request

CONTROLLER = os.environ.get("INSACLOUD_CONTROLLER", "https://127.0.0.1").rstrip("/")
TOKEN = os.environ.get("INSACLOUD_AGENT_TOKEN", "")
INTERVAL = int(os.environ.get("INSACLOUD_HEARTBEAT_INTERVAL", "30"))
TIMEOUT_HTTP = int(os.environ.get("INSACLOUD_AGENT_HTTP_TIMEOUT", "10"))
# Le contrôleur présente un certificat auto-signé sur un réseau privé : on
# l'accepte explicitement plutôt que de désactiver TLS. Mettre 0 dès qu'un
# certificat reconnu est en place.
INSECURE = os.environ.get("INSACLOUD_AGENT_INSECURE", "1") == "1"

logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                    format="%(asctime)s [%(levelname)s] agent: %(message)s")
log = logging.getLogger("insacloud.agent")

_arret = threading.Event()


# =============================================================================
#  Description du nœud
# =============================================================================
def nom_du_noeud() -> str:
    return os.environ.get("INSACLOUD_WORKER_NAME") or socket.gethostname()


def adresse_du_noeud() -> str:
    """
    Adresse que le contrôleur utilisera pour joindre ce nœud.

    Sans valeur explicite, on ouvre une socket UDP vers le contrôleur — sans
    rien envoyer — pour que le système choisisse l'interface de sortie. C'est
    la seule façon fiable d'obtenir la bonne adresse sur une machine qui en a
    plusieurs (réseau privé Vagrant + NAT).
    """
    explicite = os.environ.get("INSACLOUD_WORKER_IP")
    if explicite:
        return explicite
    cible = CONTROLLER.split("://")[-1].split(":")[0].split("/")[0]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sonde:
            sonde.connect((cible, 80))
            return sonde.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())


def nombre_de_coeurs() -> int:
    return os.cpu_count() or 1


def memoire_totale_mio() -> int:
    """Mémoire totale en Mio, lue dans /proc/meminfo (Linux) ou via sysctl (macOS)."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for ligne in f:
                if ligne.startswith("MemTotal:"):
                    return int(ligne.split()[1]) // 1024
    except OSError:
        pass
    try:
        sortie = subprocess.run(  # nosec B603  # noqa: S603
            ["/usr/sbin/sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5, check=False)
        if sortie.returncode == 0:
            return int(sortie.stdout.strip()) // (1024 * 1024)
    except (OSError, ValueError):
        pass
    return 0


def _docker_binaire() -> str:
    """
    Chemin absolu du client Docker, résolu une fois.

    Exécuter « docker » par son simple nom laisserait un répertoire du PATH
    détourner l'appel ; l'agent tourne en root sur les workers.
    """
    return shutil.which("docker") or "/usr/bin/docker"


def docker_operationnel() -> bool:
    """Un nœud dont le démon Docker ne répond pas n'a rien à faire dans le parc."""
    try:
        resultat = subprocess.run(  # nosec B603  # noqa: S603
            [_docker_binaire(), "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=15, check=False)
        return resultat.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def conteneurs_actifs() -> int:
    try:
        resultat = subprocess.run(  # nosec B603  # noqa: S603
            [_docker_binaire(), "ps", "--filter",
             "label=insacloud.role=rented-machine", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=15, check=False)
        if resultat.returncode != 0:
            return 0
        return len([x for x in resultat.stdout.splitlines() if x.strip()])
    except (OSError, subprocess.TimeoutExpired):
        return 0


# =============================================================================
#  Dialogue avec le contrôleur
# =============================================================================
def _contexte_ssl():
    if not CONTROLLER.startswith("https"):
        return None
    contexte = ssl.create_default_context()
    if INSECURE:
        # Réseau privé, certificat auto-signé émis par le rôle tls_cert.
        contexte.check_hostname = False
        contexte.verify_mode = ssl.CERT_NONE
    return contexte


def appeler(chemin: str, charge: dict):
    """POST JSON authentifié. Retourne (code, corps) ou (None, message d'erreur)."""
    # noqa: S310 — l'URL vient de CONTROLLER, variable d'environnement déposée
    # par Ansible. Le schéma est vérifié explicitement juste au-dessus pour
    # qu'aucun « file: » ne puisse s'y glisser.
    if not CONTROLLER.startswith(("http://", "https://")):
        return None, {"error": f"Adresse de contrôleur invalide : {CONTROLLER}"}
    requete = urllib.request.Request(  # noqa: S310
        f"{CONTROLLER}{chemin}",
        data=json.dumps(charge).encode(),
        headers={"Content-Type": "application/json",
                 "X-Agent-Token": TOKEN,
                 "Accept": "application/json"},
        method="POST")
    try:
        # nosec B310 / noqa: S310 — l'URL est construite à partir de CONTROLLER,
        # une variable d'environnement déposée par Ansible : ni http ni https
        # ne peuvent être remplacés par un schéma « file: » par un tiers.
        with urllib.request.urlopen(  # nosec B310  # noqa: S310
                requete, timeout=TIMEOUT_HTTP, context=_contexte_ssl()) as reponse:
            return reponse.status, json.loads(reponse.read().decode() or "{}")
    except urllib.error.HTTPError as erreur:
        try:
            corps = json.loads(erreur.read().decode() or "{}")
        except (ValueError, OSError):
            corps = {}
        return erreur.code, corps
    except (urllib.error.URLError, OSError, ValueError) as erreur:
        return None, {"error": str(erreur)}


def enregistrer() -> bool:
    """Présente le nœud au contrôleur. Retourne True si l'inscription a pris."""
    charge = {
        "hostname": nom_du_noeud(),
        "ip": adresse_du_noeud(),
        "cpu": nombre_de_coeurs(),
        "memory": memoire_totale_mio(),
        "docker": docker_operationnel(),
    }
    code, corps = appeler("/workers/register", charge)
    if code in (200, 201):
        log.info("Enregistré auprès de %s : %s (%s cœurs, %s Mio, capacité %s)",
                 CONTROLLER, charge["hostname"], charge["cpu"], charge["memory"],
                 corps.get("capacity"))
        return True
    log.error("Enregistrement refusé (%s) : %s", code, corps.get("error", corps))
    return False


def battre() -> bool:
    """
    Envoie un battement de cœur. Annonce BUSY si le nœud est saturé, pour que
    l'ordonnanceur cesse d'y envoyer des machines sans le déclarer en panne.
    """
    if not docker_operationnel():
        log.warning("Docker ne répond pas : aucun battement envoyé.")
        return False
    charge = {"hostname": nom_du_noeud(), "containers": conteneurs_actifs()}
    code, corps = appeler("/workers/heartbeat", charge)
    if code == 200:
        return True
    if code == 404 and corps.get("action") == "register":
        log.warning("Nœud inconnu du contrôleur : nouvelle inscription.")
        return enregistrer()
    log.warning("Battement refusé (%s) : %s", code, corps.get("error", corps))
    return False


# =============================================================================
#  Boucle principale
# =============================================================================
def tourner() -> int:
    if not TOKEN:
        log.error("INSACLOUD_AGENT_TOKEN n'est pas défini : arrêt.")
        return 2
    log.info("Agent démarré — contrôleur %s, battement toutes les %s s",
             CONTROLLER, INTERVAL)

    # L'inscription est retentée jusqu'à ce qu'elle passe : au démarrage d'un
    # cluster, le worker peut être prêt avant le contrôleur.
    attente = 5
    while not _arret.is_set() and not enregistrer():
        log.info("Nouvelle tentative dans %s s.", attente)
        _arret.wait(attente)
        attente = min(attente * 2, 60)

    while not _arret.is_set():
        _arret.wait(INTERVAL)
        if not _arret.is_set():
            battre()
    log.info("Agent arrêté.")
    return 0


def _signal(signum, _frame):
    log.info("Signal %s reçu, arrêt en cours.", signum)
    _arret.set()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)
    sys.exit(tourner())
