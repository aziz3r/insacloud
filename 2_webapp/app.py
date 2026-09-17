# -*- coding: utf-8 -*-
"""
app.py - Serveur web Flask d'InsaCloud (mini-fournisseur de Cloud).

Fonctionnalités :
  - Inscription / connexion des utilisateurs (mots de passe hachés).
  - Location d'une machine (conteneur Docker Ubuntu + SSH) pour N minutes.
  - Tableau de bord : commande SSH exacte, mot de passe, temps restant,
    état réel du conteneur (docker inspect), prolongation, destruction anticipée.

Interaction avec Docker : uniquement via `subprocess` (commande `docker`),
comme demandé dans le cahier des charges. Commande de création utilisée :

    docker run -d --restart=always -p <port_aleatoire>:22 <nom_image>
      (+ --name et -e ROOT_PASSWORD pour identifier la machine et
         lui donner un mot de passe unique)

Lancement en développement :
    python3 app.py
Variables d'environnement utiles (toutes optionnelles) :
    INSACLOUD_HOST, INSACLOUD_PORT, INSACLOUD_DEBUG
    INSACLOUD_IMAGE, INSACLOUD_PORT_MIN, INSACLOUD_PORT_MAX
    INSACLOUD_MAX_INSTANCES, INSACLOUD_MAX_DURATION
    INSACLOUD_SSH_HOST, INSACLOUD_SECRET_KEY, INSACLOUD_EMBED_FAUCHEUR
"""

import functools
import logging
import os
import random
import re
import secrets
import socket
import string
import subprocess
import sys

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import database as db

# =============================================================================
# Configuration
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Distributions proposées : clé du formulaire -> (image Docker, libellé affiché)
# Les images sont construites à partir de 1_docker/Dockerfile et alpine.Dockerfile.
# Limite mémoire par machine louée (vide = pas de limite)
CONTAINER_MEMORY = os.environ.get("INSACLOUD_CONTAINER_MEMORY", "256m")
GUI_CONTAINER_MEMORY = os.environ.get("INSACLOUD_GUI_MEMORY", "1g")   # un bureau a besoin de plus
TERM_CONTAINER_PORT = 7681   # port du terminal web (ttyd) dans toutes les images
GUI_CONTAINER_PORT = 6080    # port noVNC dans les images "bureau"

# Distributions disponibles (images construites depuis 1_docker/*.Dockerfile)
DISTROS = {
    "ubuntu": {"label": "Ubuntu 22.04",      "hint": "La plus répandue, outils familiers"},
    "debian": {"label": "Debian 12",         "hint": "Stable et sobre, base de nombreux serveurs"},
    "alpine": {"label": "Alpine Linux 3.20", "hint": "Ultra-légère, démarre instantanément"},
}
# Modes : chaque distribution existe en version terminal ou bureau graphique
MODES = {
    "terminal": {"label": "Terminal", "hint": "SSH + terminal dans le navigateur",
                 "gui": False, "memory": CONTAINER_MEMORY},
    "desktop":  {"label": "Bureau graphique", "hint": "XFCE et Firefox dans le navigateur, + SSH",
                 "gui": True, "memory": GUI_CONTAINER_MEMORY},
}
DEFAULT_OS = "ubuntu"
DEFAULT_MODE = "terminal"


def image_for(distro: str, mode: str) -> str:
    """
    Nom de l'image Docker pour un couple (distribution, mode) :
        insacloud_ubuntu:latest / insacloud_ubuntu_desktop:latest, etc.
    Surchargeable par variable d'environnement (INSACLOUD_IMAGE_UBUNTU_DESKTOP…).
    """
    suffix = "_desktop" if MODES[mode]["gui"] else ""
    default = f"insacloud_{distro}{suffix}:latest"
    return os.environ.get(f"INSACLOUD_IMAGE_{distro.upper()}{suffix.upper()}", default)

# Plage de ports hôte dans laquelle on tire un port aléatoire pour le SSH
PORT_MIN = int(os.environ.get("INSACLOUD_PORT_MIN", "8000"))
PORT_MAX = int(os.environ.get("INSACLOUD_PORT_MAX", "9000"))

# Règles métier
MAX_INSTANCES_PER_USER = int(os.environ.get("INSACLOUD_MAX_INSTANCES", "3"))
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = int(os.environ.get("INSACLOUD_MAX_DURATION", "120"))
DURATION_CHOICES = [5, 10, 15, 30, 60]          # proposées dans le formulaire
EXTEND_CHOICES = [5, 10, 30]                    # prolongations proposées

# Hôte affiché dans la commande SSH. Par défaut : l'hôte de l'URL courante
# (ex: 192.168.56.10 si on consulte http://192.168.56.10:5000).
SSH_HOST_OVERRIDE = os.environ.get("INSACLOUD_SSH_HOST")

CONTAINER_PREFIX = "insacloud_"
DOCKER_TIMEOUT = 60                             # secondes
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$")  # compatible noms Docker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("insacloud")


def load_secret_key() -> str:
    """
    Clé secrète des sessions Flask.
    Priorité : variable d'environnement > fichier .secret_key (créé une fois
    pour toutes pour ne pas déconnecter les utilisateurs à chaque redémarrage).
    """
    key = os.environ.get("INSACLOUD_SECRET_KEY")
    if key:
        return key
    path = os.path.join(BASE_DIR, ".secret_key")
    try:
        with open(path, "r", encoding="utf-8") as f:
            key = f.read().strip()
            if key:
                return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


app = Flask(__name__)
app.config["SECRET_KEY"] = load_secret_key()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# =============================================================================
# Couche Docker (appels subprocess)
# =============================================================================
class DockerError(Exception):
    """Erreur renvoyée par la commande docker (message lisible pour l'utilisateur)."""


def run_docker(*args, timeout: int = DOCKER_TIMEOUT) -> str:
    """Exécute `docker <args>` et retourne stdout, ou lève DockerError."""
    cmd = ["docker", *args]
    log.debug("Exécution : %s", " ".join(cmd))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise DockerError("La commande 'docker' est introuvable sur le serveur.")
    except subprocess.TimeoutExpired:
        raise DockerError("Docker n'a pas répondu dans le délai imparti.")
    if res.returncode != 0:
        message = res.stderr.strip() or f"docker {args[0]} a échoué (code {res.returncode})"
        raise DockerError(message)
    return res.stdout.strip()


def docker_run_container(port: int, name: str, root_password: str, os_type: str,
                         mode: str, term_port: int, gui_port: int = None) -> str:
    """
    Crée et démarre une machine louée. Retourne l'ID court du conteneur.
    Le couple (os_type, mode) choisi par l'utilisateur détermine l'image passée
    à `docker run` ; `term_port` est publié vers le terminal web (7681) et,
    en mode bureau, `gui_port` vers noVNC (6080).

    Commande imposée par le cahier des charges :
        docker run -d --restart=always -p <port>:22 <image>
    --restart=always : si le processus interne (sshd) plante, le démon Docker
    relance le conteneur immédiatement -> Haute Disponibilité.
    """
    spec = MODES[mode]
    args = [
        "run", "-d",
        "--restart=always",
        "-p", f"{port}:22",                                  # SSH
        "-p", f"{term_port}:{TERM_CONTAINER_PORT}",          # terminal web (ttyd)
        "--name", name,
        "-e", f"ROOT_PASSWORD={root_password}",
    ]
    if spec["gui"] and gui_port:
        args += ["-p", f"{gui_port}:{GUI_CONTAINER_PORT}"]   # bureau graphique (noVNC)
        args += ["--shm-size", "512m"]                        # confort pour Firefox
    if spec["memory"]:
        args += ["--memory", spec["memory"]]
    args.append(image_for(os_type, mode))   # <- image choisie dynamiquement
    full_id = run_docker(*args)
    return full_id[:12]


def docker_remove_container(container_id: str) -> None:
    """Détruit un conteneur (`docker rm -f`). Ne se plaint pas s'il n'existe plus."""
    try:
        run_docker("rm", "-f", container_id)
    except DockerError as exc:
        if "No such container" in str(exc):
            return
        raise


def docker_container_state(container_id: str) -> str:
    """
    État réel du conteneur vu par Docker : running / restarting / exited / …
    Retourne 'absent' si le conteneur n'existe plus.
    """
    try:
        return run_docker("inspect", "-f", "{{.State.Status}}", container_id, timeout=10)
    except DockerError:
        return "absent"


# =============================================================================
# Utilitaires métier
# =============================================================================
def port_is_free_on_host(port: int) -> bool:
    """Vérifie qu'aucun service n'écoute déjà sur ce port de l'hôte."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def choose_random_port(exclude: set = frozenset()) -> int:
    """
    Tire un port aléatoire dans [PORT_MIN, PORT_MAX] qui n'est ni réservé
    par une instance active en BDD, ni occupé sur l'hôte, ni dans `exclude`
    (ports déjà attribués dans la même requête).
    """
    for _ in range(100):
        port = random.randint(PORT_MIN, PORT_MAX)
        if port not in exclude and not db.port_in_use(port) and port_is_free_on_host(port):
            return port
    raise DockerError("Aucun port libre disponible dans la plage configurée.")


def generate_password(length: int = 12) -> str:
    """Mot de passe root aléatoire, sans caractères ambigus (0/O, l/1)."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def ssh_host() -> str:
    """Hôte à utiliser dans la commande SSH affichée à l'utilisateur."""
    if SSH_HOST_OVERRIDE:
        return SSH_HOST_OVERRIDE
    return request.host.split(":")[0]


def parse_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# =============================================================================
# Authentification
# =============================================================================
@app.before_request
def load_current_user():
    """Charge l'utilisateur connecté dans `g.user` à chaque requête."""
    user_id = session.get("user_id")
    g.user = db.get_user_by_id(user_id) if user_id else None


def login_required(view):
    """Décorateur : redirige vers /login si l'utilisateur n'est pas connecté."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Veuillez vous connecter pour accéder à cette page.", "info")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
def index():
    return redirect(url_for("dashboard" if g.user else "login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_username(username)
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Identifiant ou mot de passe incorrect.", "error")
            return render_template("login.html", active_tab="login"), 401
        session.clear()
        session["user_id"] = user["id"]
        log.info("Connexion de l'utilisateur '%s'", username)
        flash(f"Bienvenue, {username} !", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html", active_tab="login")


@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm", "")

    if not USERNAME_RE.match(username):
        flash("Identifiant invalide : 3 à 32 caractères (lettres, chiffres, . _ -), "
              "commençant par une lettre ou un chiffre.", "error")
        return render_template("login.html", active_tab="register"), 400
    if len(password) < 6:
        flash("Le mot de passe doit contenir au moins 6 caractères.", "error")
        return render_template("login.html", active_tab="register"), 400
    if password != confirm:
        flash("Les deux mots de passe ne correspondent pas.", "error")
        return render_template("login.html", active_tab="register"), 400

    user_id = db.create_user(username, generate_password_hash(password))
    if user_id is None:
        flash("Cet identifiant est déjà utilisé.", "error")
        return render_template("login.html", active_tab="register"), 409

    session.clear()
    session["user_id"] = user_id
    log.info("Nouvel utilisateur inscrit : '%s'", username)
    flash("Compte créé avec succès. Bienvenue sur InsaCloud !", "success")
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Vous avez été déconnecté.", "info")
    return redirect(url_for("login"))


# =============================================================================
# Tableau de bord et gestion des machines
# =============================================================================
@app.route("/dashboard")
@login_required
def dashboard():
    now = db.utc_now()
    host = ssh_host()
    instances = []
    for row in db.get_user_instances(g.user["id"]):
        inst = dict(row)
        expires = db.parse_utc(inst["expires_at"])
        created = db.parse_utc(inst["created_at"])
        is_running = inst["status"] == db.STATUS_RUNNING
        inst["is_running"] = is_running
        inst["remaining_seconds"] = max(0, int((expires - now).total_seconds())) if is_running else 0
        # Durée totale de la location (pour la barre de progression du dashboard)
        inst["total_seconds"] = max(1, int((expires - created).total_seconds()))
        inst["expires_at_local"] = db.to_local(expires).strftime("%H:%M:%S")
        inst["expires_date_local"] = db.to_local(expires).strftime("%d/%m/%Y %H:%M")
        inst["created_at_local"] = db.to_local(created).strftime("%d/%m/%Y %H:%M")
        inst["ssh_command"] = f"ssh root@{host} -p {inst['port']}"
        inst["os_label"] = DISTROS.get(inst["os_type"], {}).get("label", inst["os_type"])
        inst["mode_label"] = MODES.get(inst["mode"], {}).get("label", inst["mode"])
        inst["is_desktop"] = bool(inst["gui_port"])
        # Terminal web (toutes les machines) et bureau graphique (mode desktop)
        inst["term_url"] = f"http://{host}:{inst['term_port']}/" if inst["term_port"] else None
        inst["gui_url"] = (f"http://{host}:{inst['gui_port']}/vnc.html?autoconnect=true&resize=remote"
                           if inst["gui_port"] else None)
        inst["vnc_password"] = inst["root_password"][:8]   # limite du protocole VNC
        # État réel côté Docker (démontre la Haute Disponibilité : "restarting" si crash)
        inst["docker_state"] = docker_container_state(inst["container_id"]) if is_running else "-"
        instances.append(inst)

    active = [i for i in instances if i["is_running"]]
    history = [i for i in instances if not i["is_running"]]
    return render_template(
        "dashboard.html",
        active_instances=active,
        history_instances=history,
        active_count=len(active),
        max_instances=MAX_INSTANCES_PER_USER,
        duration_choices=DURATION_CHOICES,
        extend_choices=EXTEND_CHOICES,
        max_duration=MAX_DURATION_MINUTES,
        distros=DISTROS,
        modes=MODES,
        default_os=DEFAULT_OS,
        default_mode=DEFAULT_MODE,
        ssh_host=host,
    )


@app.route("/instances/create", methods=["POST"])
@login_required
def create_instance():
    """Loue une nouvelle machine pour `duration` minutes."""
    minutes = parse_int(request.form.get("duration"))
    if not (MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES):
        flash(f"Durée invalide : choisissez entre {MIN_DURATION_MINUTES} et "
              f"{MAX_DURATION_MINUTES} minutes.", "error")
        return redirect(url_for("dashboard"))

    # Distribution et mode choisis par l'utilisateur (listes blanches)
    os_type = request.form.get("os", DEFAULT_OS)
    mode = request.form.get("mode", DEFAULT_MODE)
    if os_type not in DISTROS or mode not in MODES:
        flash("Distribution ou mode inconnu.", "error")
        return redirect(url_for("dashboard"))

    if db.count_active_instances(g.user["id"]) >= MAX_INSTANCES_PER_USER:
        flash(f"Quota atteint : vous ne pouvez pas louer plus de "
              f"{MAX_INSTANCES_PER_USER} machines simultanément.", "error")
        return redirect(url_for("dashboard"))

    # 1) Préparation : port(s) libre(s), nom unique, mot de passe unique
    try:
        port = choose_random_port()
        term_port = choose_random_port(exclude={port})
        gui_port = choose_random_port(exclude={port, term_port}) if MODES[mode]["gui"] else None
    except DockerError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    name = f"{CONTAINER_PREFIX}{g.user['username']}_{secrets.token_hex(3)}"
    root_password = generate_password()

    # 2) Création du conteneur
    try:
        container_id = docker_run_container(port, name, root_password, os_type, mode, term_port, gui_port)
    except DockerError as exc:
        log.error("Échec de création du conteneur pour '%s' : %s", g.user["username"], exc)
        flash(f"Impossible de créer la machine : {exc}", "error")
        return redirect(url_for("dashboard"))

    # 3) Enregistrement en BDD. En cas d'échec, on détruit le conteneur
    #    pour ne pas laisser de machine orpheline.
    try:
        db.create_instance(g.user["id"], container_id, name, port, root_password, minutes,
                           os_type, mode, term_port, gui_port)
    except Exception as exc:  # noqa: BLE001
        log.exception("Échec d'enregistrement en BDD, suppression du conteneur %s", container_id)
        try:
            docker_remove_container(container_id)
        except DockerError:
            pass
        flash(f"Erreur interne lors de l'enregistrement : {exc}", "error")
        return redirect(url_for("dashboard"))

    log.info("Machine créée : %s (%s/%s, id=%s, port=%s, %s min) pour '%s'",
             name, os_type, mode, container_id, port, minutes, g.user["username"])
    flash(f"Machine « {name} » ({DISTROS[os_type]['label']}, {MODES[mode]['label'].lower()}) "
          f"louée pour {minutes} minute(s).", "success")
    return redirect(url_for("dashboard"))


@app.route("/instances/<int:instance_id>/delete", methods=["POST"])
@login_required
def delete_instance(instance_id):
    """Rend la machine avant la fin de la location (docker rm -f)."""
    inst = db.get_instance(instance_id, user_id=g.user["id"])
    if inst is None:
        abort(404)
    if inst["status"] != db.STATUS_RUNNING:
        flash("Cette machine n'est plus active.", "info")
        return redirect(url_for("dashboard"))

    try:
        docker_remove_container(inst["container_id"])
    except DockerError as exc:
        log.error("Échec de suppression du conteneur %s : %s", inst["container_id"], exc)
        flash(f"Impossible de supprimer la machine : {exc}", "error")
        return redirect(url_for("dashboard"))

    db.set_instance_status(instance_id, db.STATUS_STOPPED)
    log.info("Machine %s rendue par '%s'", inst["container_name"], g.user["username"])
    flash(f"Machine « {inst['container_name']} » supprimée.", "success")
    return redirect(url_for("dashboard"))


@app.route("/instances/<int:instance_id>/extend", methods=["POST"])
@login_required
def extend_instance(instance_id):
    """Prolonge la location d'une machine active."""
    inst = db.get_instance(instance_id, user_id=g.user["id"])
    if inst is None:
        abort(404)
    if inst["status"] != db.STATUS_RUNNING:
        flash("Impossible de prolonger une machine inactive.", "error")
        return redirect(url_for("dashboard"))

    minutes = parse_int(request.form.get("minutes"))
    if not (MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES):
        flash("Durée de prolongation invalide.", "error")
        return redirect(url_for("dashboard"))

    # On borne le temps restant total pour éviter les locations "infinies"
    remaining = (db.parse_utc(inst["expires_at"]) - db.utc_now()).total_seconds() / 60
    if remaining + minutes > MAX_DURATION_MINUTES:
        flash(f"Le temps restant ne peut pas dépasser {MAX_DURATION_MINUTES} minutes.", "error")
        return redirect(url_for("dashboard"))

    db.extend_instance(instance_id, minutes)
    flash(f"Location prolongée de {minutes} minute(s).", "success")
    return redirect(url_for("dashboard"))


# =============================================================================
# Gestion d'erreurs
# =============================================================================
@app.errorhandler(404)
def not_found(_error):
    flash("Ressource introuvable.", "error")
    return redirect(url_for("dashboard" if g.get("user") else "login"))


# =============================================================================
# Démarrage
# =============================================================================
db.init_db()  # crée le schéma au chargement du module (idempotent)

if __name__ == "__main__":
    host = os.environ.get("INSACLOUD_HOST", "0.0.0.0")
    port = int(os.environ.get("INSACLOUD_PORT", "5000"))
    debug = os.environ.get("INSACLOUD_DEBUG", "0") == "1"

    # Mode "tout-en-un" pratique en développement : le Faucheur tourne dans un
    # thread du serveur Flask. En production (Ansible/systemd) il tourne dans
    # son propre service, donc cette variable n'est pas définie.
    if os.environ.get("INSACLOUD_EMBED_FAUCHEUR", "0") == "1":
        # Avec le rechargeur de Flask en debug, le module est chargé deux fois :
        # on ne démarre le thread que dans le processus "fils" qui sert les requêtes.
        if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            import faucheur
            faucheur.start_in_background()
            log.info("Faucheur démarré en thread d'arrière-plan.")

    log.info("InsaCloud démarre sur http://%s:%s (distributions=%s, modes=%s)", host, port,
             ", ".join(DISTROS), ", ".join(MODES))
    app.run(host=host, port=port, debug=debug)
