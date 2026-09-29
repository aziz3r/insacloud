"""
api.py - API JSON d'InsaCloud.

Les chemins et les verbes sont exactement ceux du cahier des charges :

    GET  /health                    état du service (sans authentification)
    GET  /instances                 locations de l'utilisateur connecté
    POST /rent                      louer une machine
    POST /instances/<id>/stop       arrêter une machine
    GET  /workers                   parc de nœuds d'exécution
    GET  /workers/<id>              un nœud
    POST /workers/register          un worker se déclare (agent)
    POST /workers/heartbeat         battement de cœur d'un worker (agent)

/register, /login, /logout et /dashboard sont servis par app.py : ces routes
répondent en JSON quand le client le demande (en-tête Accept ou Content-Type),
et en HTML sinon. Un même chemin, deux représentations.

Authentification :
  * routes utilisateur  -> la session de connexion du site ;
  * routes /workers/... -> un jeton partagé (INSACLOUD_AGENT_TOKEN), déposé par
    Ansible depuis son Vault. Les agents ne sont pas des comptes utilisateur.
"""

import functools
import hmac
import logging
import os

from flask import Blueprint, g, jsonify, request

import database as db
import services
from config import MODES

log = logging.getLogger("insacloud.api")

api = Blueprint("api", __name__)

# Jeton partagé des agents. Vide = les routes d'enregistrement sont fermées,
# ce qui est le comportement sûr par défaut si Ansible n'a rien déposé.
AGENT_TOKEN = os.environ.get("INSACLOUD_AGENT_TOKEN", "")

VERSION = "1.0.0"


def sans_csrf(vue):
    """
    Marque une route comme dispensée du jeton CSRF.

    Légitime uniquement pour les routes d'agent : elles s'authentifient par un
    jeton porté dans un en-tête, jamais par un cookie. Or une attaque CSRF
    repose précisément sur l'envoi automatique du cookie par le navigateur.
    """
    vue._csrf_exempt = True
    return vue


def token_agent_requis(vue):
    """Vérifie le jeton partagé des agents, en comparaison à temps constant."""

    @functools.wraps(vue)
    def enveloppe(*args, **kwargs):
        if not AGENT_TOKEN:
            return jsonify(error="L'enregistrement des workers est désactivé "
                                 "(INSACLOUD_AGENT_TOKEN non défini)."), 503
        fourni = request.headers.get("X-Agent-Token", "")
        if not fourni:
            entete = request.headers.get("Authorization", "")
            if entete.lower().startswith("bearer "):
                fourni = entete[7:]
        if not hmac.compare_digest(fourni, AGENT_TOKEN):
            log.warning("Jeton d'agent refusé depuis %s", request.remote_addr)
            return jsonify(error="Jeton d'agent invalide."), 401
        return vue(*args, **kwargs)

    return enveloppe


def connexion_requise(vue):
    """Réserve la route à un utilisateur connecté, en répondant en JSON."""

    @functools.wraps(vue)
    def enveloppe(*args, **kwargs):
        if g.get("user") is None:
            return jsonify(error="Authentification requise."), 401
        return vue(*args, **kwargs)

    return enveloppe


def _instance_publique(instance: dict) -> dict:
    """
    Représentation d'une location renvoyée par l'API.
    Le mot de passe root n'y figure jamais : il ne sort que par le coffre.
    """
    return {
        "id": instance["id"],
        "name": instance["container_name"],
        "container_id": instance["container_id"],
        "distribution": instance["os_type"],
        "mode": instance["mode"],
        "status": instance["status"],
        "worker": instance["worker"],
        "worker_ip": instance["worker_ip"],
        "ssh_port": instance["port"],
        "ssh_command": f"ssh root@{instance['worker_ip'] or '127.0.0.1'} -p {instance['port']}",
        "term_port": instance["term_port"],
        "gui_port": instance["gui_port"],
        "created_at": str(instance["created_at"]),
        "start_time": str(instance["start_time"]),
        "expires_at": str(instance["expires_at"]),
        "rental_id": instance["rental_id"],
        "rental_status": instance["rental_status"],
        "migrations": instance.get("migrations", 0),
    }


# =============================================================================
#  État du service
# =============================================================================
@api.route("/health")
def health():
    """
    Sonde de santé : utilisée par le healthcheck Docker, le watchdog systemd
    et l'étape de déploiement de l'intégration continue.
    """
    try:
        statistiques = db.get_statistics()
        base = "ok"
    except Exception as exc:  # noqa: BLE001 - la sonde ne doit jamais lever
        log.error("Sonde de santé : base injoignable (%s)", exc)
        return jsonify(status="degraded", database="unreachable", version=VERSION), 503
    return jsonify(status="ok", database=base, version=VERSION, **statistiques)


# =============================================================================
#  Locations
# =============================================================================
@api.route("/instances")
@connexion_requise
def liste_instances():
    """Toutes les locations de l'utilisateur connecté."""
    instances = db.get_user_instances(g.user["id"])
    return jsonify(count=len(instances),
                   instances=[_instance_publique(i) for i in instances])


@api.route("/instances/<int:instance_id>", methods=["GET"])
@connexion_requise
def detail_instance(instance_id):
    instance = db.get_instance(instance_id, g.user["id"])
    if instance is None:
        return jsonify(error="Machine introuvable."), 404
    return jsonify(_instance_publique(instance))


@api.route("/rent", methods=["POST"])
@connexion_requise
def louer():
    """
    Loue une machine.

    Corps JSON : {"distribution": "alpine", "mode": "terminal", "duration": 15}
    Le mot de passe root n'est renvoyé qu'ici, une seule fois — comme sur
    l'interface web, il n'est ensuite plus lisible que par le coffre.
    """
    donnees = request.get_json(silent=True) or request.form
    try:
        duree = int(donnees.get("duration", 0))
    except (TypeError, ValueError):
        return jsonify(error="Durée invalide."), 400

    resultat = services.louer(
        g.user, duree,
        donnees.get("distribution") or donnees.get("os"),
        donnees.get("mode"))
    if not resultat.ok:
        return jsonify(error=resultat.message), resultat.code

    instance = db.get_instance(resultat.donnees["instance_id"], g.user["id"])
    reponse = _instance_publique(instance)
    reponse["root_password"] = resultat.donnees["password"]
    if resultat.donnees.get("is_desktop"):
        reponse["vnc_password"] = resultat.donnees["password"][:8]
    reponse["message"] = resultat.message
    return jsonify(reponse), 201


@api.route("/instances/<int:instance_id>/stop", methods=["POST"])
@connexion_requise
def arreter(instance_id):
    """Arrête et détruit une machine avant son échéance."""
    resultat = services.arreter(instance_id, g.user["id"])
    if not resultat.ok:
        return jsonify(error=resultat.message), resultat.code
    return jsonify(status="stopped", message=resultat.message)


# =============================================================================
#  Catalogue
# =============================================================================
@api.route("/distributions")
def liste_distributions():
    """Distributions proposables, telles que l'interface les affiche."""
    catalogue = services.distributions_disponibles()
    return jsonify(count=len(catalogue), distributions=[
        {"name": nom, **fiche} for nom, fiche in catalogue.items()],
        modes=[{"name": nom, "label": spec["label"], "gui": spec["gui"]}
               for nom, spec in MODES.items()])


# =============================================================================
#  Parc de workers
# =============================================================================
@api.route("/workers")
def liste_workers():
    """
    État du parc. Ouvert en lecture : aucune donnée d'utilisateur n'y figure,
    et c'est ce que consultent le tableau de supervision et la CI.
    """
    workers = db.get_workers()
    return jsonify(count=len(workers), workers=[{
        "id": w["id"], "hostname": w["hostname"], "ip": w["ip"],
        "status": w["status"], "cpu": w["cpu"], "memory": w["memory"],
        "capacity": w["capacity"], "running_instances": w["running_instances"],
        "reachable": w["reachable"],
        "last_heartbeat": str(w["last_heartbeat"]) if w["last_heartbeat"] else None,
    } for w in workers])


@api.route("/workers/<int:worker_id>")
def detail_worker(worker_id):
    worker = db.get_worker(worker_id)
    if worker is None:
        return jsonify(error="Worker introuvable."), 404
    return jsonify({
        "id": worker["id"], "hostname": worker["hostname"], "ip": worker["ip"],
        "status": worker["status"], "cpu": worker["cpu"], "memory": worker["memory"],
        "capacity": worker["capacity"], "running_instances": worker["running_instances"],
        "reachable": worker["reachable"],
        "last_heartbeat": str(worker["last_heartbeat"]) if worker["last_heartbeat"] else None,
        "instances": [_instance_publique(i)
                      for i in db.get_instances_on_worker(worker["hostname"])],
    })


@api.route("/workers/register", methods=["POST"])
@sans_csrf
@token_agent_requis
def enregistrer_worker():
    """
    Un worker se déclare au démarrage (worker_agent.py).

    Corps JSON : {"hostname": "worker1", "ip": "192.168.56.11",
                  "cpu": 2, "memory": 3072, "docker": true}
    """
    donnees = request.get_json(silent=True) or {}
    hostname = (donnees.get("hostname") or "").strip()
    ip = (donnees.get("ip") or request.remote_addr or "").strip()
    if not hostname or not ip:
        return jsonify(error="hostname et ip sont obligatoires."), 400
    if not donnees.get("docker", True):
        return jsonify(error="Docker n'est pas opérationnel sur ce nœud."), 400

    worker = db.register_worker(
        hostname=hostname, ip=ip,
        cpu=donnees.get("cpu"), memory=donnees.get("memory"),
        capacity=donnees.get("capacity"))
    log.info("Worker enregistré : %s (%s, %s cœurs, %s Mio, capacité %s)",
             hostname, ip, worker["cpu"], worker["memory"], worker["capacity"])
    return jsonify(id=worker["id"], hostname=worker["hostname"],
                   status=worker["status"], capacity=worker["capacity"],
                   message="Worker enregistré."), 201


@api.route("/workers/heartbeat", methods=["POST"])
@sans_csrf
@token_agent_requis
def battement_de_coeur():
    """
    Battement de cœur d'un worker. Son absence prolongée le fait passer
    OFFLINE, ce qui déclenche la reprise de ses instances.
    """
    donnees = request.get_json(silent=True) or {}
    hostname = (donnees.get("hostname") or "").strip()
    if not hostname:
        return jsonify(error="hostname est obligatoire."), 400
    if not db.worker_heartbeat(hostname, donnees.get("status")):
        # Inconnu : l'agent doit se réenregistrer (cas d'une base réinitialisée)
        return jsonify(error="Worker inconnu, appelez /workers/register.",
                       action="register"), 404
    return jsonify(status="ok", hostname=hostname)
