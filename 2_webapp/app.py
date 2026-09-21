# -*- coding: utf-8 -*-
"""
app.py - Serveur web Flask d'InsaCloud (mini-fournisseur de Cloud).

Fonctionnalités :
  - Inscription / connexion (mots de passe hachés, anti-force-brute, CSRF).
  - Location d'une machine (conteneur Docker) : 3 distributions × 2 modes
    (terminal ou bureau graphique), pour N minutes.
  - Tableau de bord : commande SSH, terminal web, bureau, temps restant,
    état réel du conteneur, prolongation, rotation du mot de passe, destruction.

Sécurité (voir aussi README, section « Sécurité ») :
  - le mot de passe root d'une machine est généré, affiché UNE SEULE FOIS puis
    oublié (jamais stocké) ; il peut être régénéré à chaud ;
  - clé publique SSH par utilisateur : injectée dans la machine, l'accès SSH
    par mot de passe y est alors désactivé ;
  - jetons CSRF sur tous les formulaires, déconnexion en POST ;
  - limitation des tentatives de connexion (par compte et par adresse IP) ;
  - en-têtes de sécurité avec CSP stricte (aucun script ni style externe) ;
  - cookies HttpOnly / Secure / SameSite=Strict, session limitée dans le temps ;
  - conteneurs lancés avec capacités minimales, no-new-privileges, limites CPU/PID.

Interaction avec Docker : uniquement via `subprocess` (commande `docker`).
Commande de création :
    docker run -d --restart=always -p <port>:22 <image>   (+ options de durcissement)

Variables d'environnement (toutes optionnelles, voir README) : INSACLOUD_*
"""

import functools
import hmac
import logging
import os
import random
import re
import secrets
import socket
import string
import subprocess
import sys
from datetime import timedelta

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import database as db

# =============================================================================
# Configuration
# =============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Plage de ports hôte dans laquelle on tire les ports publiés (SSH, terminal, bureau)
PORT_MIN = int(os.environ.get("INSACLOUD_PORT_MIN", "8000"))
PORT_MAX = int(os.environ.get("INSACLOUD_PORT_MAX", "9000"))

# Règles métier
MAX_INSTANCES_PER_USER = int(os.environ.get("INSACLOUD_MAX_INSTANCES", "3"))
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = int(os.environ.get("INSACLOUD_MAX_DURATION", "120"))
DURATION_CHOICES = [5, 10, 15, 30, 60]
EXTEND_CHOICES = [5, 10, 30]

# Anti-force-brute (connexion)
LOGIN_WINDOW_MINUTES = int(os.environ.get("INSACLOUD_LOGIN_WINDOW", "15"))
LOGIN_MAX_FAILURES_USER = int(os.environ.get("INSACLOUD_LOGIN_MAX_USER", "5"))
LOGIN_MAX_FAILURES_IP = int(os.environ.get("INSACLOUD_LOGIN_MAX_IP", "20"))

# Politique de mot de passe des comptes
PASSWORD_MIN_LENGTH = int(os.environ.get("INSACLOUD_PASSWORD_MIN", "10"))
COMMON_PASSWORDS = {"password", "motdepasse", "123456789", "1234567890", "azertyuiop",
                    "qwertyuiop", "insacloud", "administrator", "iloveyou12"}

# Mémoire / ressources des machines louées
CONTAINER_MEMORY = os.environ.get("INSACLOUD_CONTAINER_MEMORY", "256m")
GUI_CONTAINER_MEMORY = os.environ.get("INSACLOUD_GUI_MEMORY", "1g")
CONTAINER_CPUS = os.environ.get("INSACLOUD_CONTAINER_CPUS", "1")
CONTAINER_PIDS_LIMIT = os.environ.get("INSACLOUD_CONTAINER_PIDS", "512")
TERM_CONTAINER_PORT = 7681   # ttyd dans toutes les images
GUI_CONTAINER_PORT = 6080    # noVNC dans les images "bureau"

# Certificat TLS monté dans les machines (terminal web + noVNC en HTTPS) : vide = HTTP
TLS_DIR = os.environ.get("INSACLOUD_TLS_DIR", "")

# Le site est-il servi en HTTPS (derrière nginx) ? -> cookies Secure, HSTS
HTTPS = os.environ.get("INSACLOUD_HTTPS", "0") == "1"
BEHIND_PROXY = os.environ.get("INSACLOUD_BEHIND_PROXY", "0") == "1"

# Hôte affiché dans les commandes / liens. Par défaut : l'hôte de l'URL courante.
SSH_HOST_OVERRIDE = os.environ.get("INSACLOUD_SSH_HOST")

CONTAINER_PREFIX = "insacloud_"
DOCKER_TIMEOUT = 60
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$")   # compatible noms Docker
SSH_PUBKEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com)"
    r" [A-Za-z0-9+/]+=*( [^\r\n]{0,128})?$"
)

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

# Images réellement présentes sur le serveur (renseigné par Ansible selon demo_mode).
AVAILABLE_IMAGES = {x.strip() for x in os.environ.get("INSACLOUD_AVAILABLE_IMAGES", "").split(",") if x.strip()}

# Durcissement des conteneurs loués : capacités minimales pour sshd / supervisord,
# pas d'escalade de privilèges, limites de processus et de CPU.
CONTAINER_HARDENING = [
    "--cap-drop", "ALL",
    "--cap-add", "CHOWN", "--cap-add", "DAC_OVERRIDE", "--cap-add", "FOWNER",
    "--cap-add", "FSETID", "--cap-add", "SETGID", "--cap-add", "SETUID",
    "--cap-add", "SYS_CHROOT", "--cap-add", "NET_BIND_SERVICE",
    "--cap-add", "KILL", "--cap-add", "AUDIT_WRITE",
    "--security-opt", "no-new-privileges:true",
    "--pids-limit", CONTAINER_PIDS_LIMIT,
    "--cpus", CONTAINER_CPUS,
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("insacloud")


def load_secret_key() -> str:
    """Clé secrète des sessions : variable d'environnement (Ansible/Vault) ou fichier local."""
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
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,          # inaccessible à JavaScript
    SESSION_COOKIE_SAMESITE="Strict",      # jamais envoyé depuis un autre site
    SESSION_COOKIE_SECURE=HTTPS,           # HTTPS uniquement quand le site est en TLS
    SESSION_COOKIE_NAME="insacloud_session",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
    MAX_CONTENT_LENGTH=16 * 1024,          # aucune requête légitime ne dépasse 16 Ko
)
if BEHIND_PROXY:
    # Derrière nginx : faire confiance aux en-têtes X-Forwarded-* du proxy (1 saut)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# =============================================================================
# Sécurité HTTP : CSRF, en-têtes, session
# =============================================================================
def csrf_token() -> str:
    """Jeton CSRF lié à la session (généré à la première page rendue)."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.autoescape = True


@app.before_request
def security_before_request():
    """Charge l'utilisateur, vérifie le jeton CSRF sur toute requête modifiante."""
    user_id = session.get("user_id")
    g.user = db.get_user_by_id(user_id) if user_id else None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        expected = session.get("_csrf")
        received = request.form.get("_csrf", "")
        if not expected or not hmac.compare_digest(expected, received):
            log.warning("CSRF refusé : %s %s ip=%s", request.method, request.path, client_ip())
            abort(403)


@app.after_request
def security_headers(response):
    """En-têtes de sécurité. La CSP n'autorise que les ressources du site lui-même."""
    csp = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    if HTTPS:
        csp += "; upgrade-insecure-requests"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    else:
        # Les pages contiennent des données sensibles : jamais mises en cache
        response.headers["Cache-Control"] = "no-store"
    return response


def client_ip() -> str:
    return request.remote_addr or "?"


# =============================================================================
# Couche Docker (appels subprocess)
# =============================================================================
class DockerError(Exception):
    """Erreur renvoyée par la commande docker (message lisible pour l'utilisateur)."""


def run_docker(*args, timeout: int = DOCKER_TIMEOUT) -> str:
    """Exécute `docker <args>` et retourne stdout, ou lève DockerError."""
    cmd = ["docker", *args]
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


def image_for(distro: str, mode: str) -> str:
    """insacloud_<distro>[_desktop]:latest, surchargeable par INSACLOUD_IMAGE_<DISTRO>[_DESKTOP]."""
    suffix = "_desktop" if MODES[mode]["gui"] else ""
    default = f"insacloud_{distro}{suffix}:latest"
    return os.environ.get(f"INSACLOUD_IMAGE_{distro.upper()}{suffix.upper()}", default)


def image_available(distro: str, mode: str) -> bool:
    if not AVAILABLE_IMAGES:
        return True
    return image_for(distro, mode).split(":")[0] in AVAILABLE_IMAGES


def available_choices():
    distros = {k: v for k, v in DISTROS.items() if any(image_available(k, m) for m in MODES)}
    modes = {k: v for k, v in MODES.items() if any(image_available(d, k) for d in distros)}
    return distros, modes


def docker_run_container(port: int, name: str, root_password: str, os_type: str,
                         mode: str, term_port: int, gui_port: int = None,
                         ssh_public_key: str = None) -> str:
    """
    Crée et démarre une machine louée. Retourne l'ID court du conteneur.

    Commande imposée : docker run -d --restart=always -p <port>:22 <image>
    complétée par le terminal web, le bureau (mode desktop), le durcissement,
    le mot de passe (jamais journalisé) et la clé SSH éventuelle.
    """
    spec = MODES[mode]
    args = [
        "run", "-d",
        "--restart=always",
        "-p", f"{port}:22",
        "-p", f"{term_port}:{TERM_CONTAINER_PORT}",
        "--name", name,
        "-e", f"ROOT_PASSWORD={root_password}",
        *CONTAINER_HARDENING,
    ]
    if ssh_public_key:
        args += ["-e", f"SSH_PUBKEY={ssh_public_key}"]
    if TLS_DIR:
        args += ["-v", f"{TLS_DIR}:/tls:ro"]
    if spec["gui"] and gui_port:
        args += ["-p", f"{gui_port}:{GUI_CONTAINER_PORT}", "--shm-size", "512m"]
    if spec["memory"]:
        args += ["--memory", spec["memory"]]
    args.append(image_for(os_type, mode))
    return run_docker(*args)[:12]


def docker_remove_container(container_id: str) -> None:
    try:
        run_docker("rm", "-f", container_id)
    except DockerError as exc:
        if "No such container" in str(exc):
            return
        raise


def docker_container_state(container_id: str) -> str:
    try:
        return run_docker("inspect", "-f", "{{.State.Status}}", container_id, timeout=10)
    except DockerError:
        return "absent"


def docker_rotate_password(container_id: str, new_password: str) -> None:
    """Rotation à chaud : SSH, terminal web et VNC prennent le nouveau mot de passe."""
    run_docker("exec", container_id, "/usr/local/bin/entrypoint.sh", "setpass", new_password)


# =============================================================================
# Utilitaires métier
# =============================================================================
def port_is_free_on_host(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def choose_random_port(exclude: set = frozenset()) -> int:
    for _ in range(100):
        port = random.randint(PORT_MIN, PORT_MAX)
        if port not in exclude and not db.port_in_use(port) and port_is_free_on_host(port):
            return port
    raise DockerError("Aucun port libre disponible dans la plage configurée.")


def generate_password(length: int = 16) -> str:
    """Mot de passe root aléatoire (CSPRNG), sans caractères ambigus : ~90 bits d'entropie."""
    alphabet = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    return "".join(secrets.choice(alphabet) for _ in range(length))


def public_host() -> str:
    if SSH_HOST_OVERRIDE:
        return SSH_HOST_OVERRIDE
    return request.host.split(":")[0]


def machine_scheme() -> str:
    """Terminal web et bureau sont en HTTPS dès qu'un certificat est monté dans les machines."""
    return "https" if TLS_DIR else "http"


def parse_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def password_policy_error(password: str, username: str):
    """Retourne un message d'erreur si le mot de passe du compte est trop faible."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères."
    if password.lower() in COMMON_PASSWORDS or password.lower() == username.lower():
        return "Ce mot de passe est trop courant ou identique à l'identifiant."
    return None


# =============================================================================
# Authentification
# =============================================================================
def login_required(view):
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
        username = request.form.get("username", "").strip()[:32]
        password = request.form.get("password", "")
        ip = client_ip()

        # Anti-force-brute : verrouillage temporaire par compte et par adresse IP
        if (db.count_recent_failures(LOGIN_WINDOW_MINUTES, username=username) >= LOGIN_MAX_FAILURES_USER
                or db.count_recent_failures(LOGIN_WINDOW_MINUTES, ip=ip) >= LOGIN_MAX_FAILURES_IP):
            log.warning("Connexion verrouillée : user=%s ip=%s", username, ip)
            flash(f"Trop de tentatives. Réessayez dans {LOGIN_WINDOW_MINUTES} minutes.", "error")
            return render_template("login.html", active_tab="login"), 429

        user = db.get_user_by_username(username)
        # check_password_hash est en temps constant ; on le fait même si le compte n'existe pas
        ok = user is not None and check_password_hash(user["password_hash"], password)
        if not ok:
            check_password_hash(generate_password_hash("x"), password) if user is None else None
            db.record_login_attempt(username, ip, False)
            log.warning("Échec de connexion : user=%s ip=%s", username, ip)
            flash("Identifiant ou mot de passe incorrect.", "error")
            return render_template("login.html", active_tab="login"), 401

        db.record_login_attempt(username, ip, True)
        db.purge_login_attempts(24)
        session.clear()                       # nouvelle session (anti-fixation)
        session.permanent = True
        session["user_id"] = user["id"]
        log.info("Connexion : user=%s ip=%s", username, ip)
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
    error = password_policy_error(password, username)
    if error:
        flash(error, "error")
        return render_template("login.html", active_tab="register"), 400
    if password != confirm:
        flash("Les deux mots de passe ne correspondent pas.", "error")
        return render_template("login.html", active_tab="register"), 400

    user_id = db.create_user(username, generate_password_hash(password))
    if user_id is None:
        flash("Cet identifiant est déjà utilisé.", "error")
        return render_template("login.html", active_tab="register"), 409

    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    log.info("Inscription : user=%s ip=%s", username, client_ip())
    flash("Compte créé avec succès. Bienvenue sur InsaCloud !", "success")
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Vous avez été déconnecté.", "info")
    return redirect(url_for("login"))


# =============================================================================
# Profil : clé publique SSH
# =============================================================================
@app.route("/profile/ssh-key", methods=["POST"])
@login_required
def set_ssh_key():
    key = " ".join(request.form.get("ssh_public_key", "").strip().split())
    if key and (len(key) > 1024 or not SSH_PUBKEY_RE.match(key)):
        flash("Clé publique invalide : attendu « ssh-ed25519 AAAA… », « ssh-rsa AAAA… » ou « ecdsa-sha2-… ».", "error")
        return redirect(url_for("dashboard"))
    db.set_user_ssh_key(g.user["id"], key)
    log.info("Clé SSH %s : user=%s", "enregistrée" if key else "supprimée", g.user["username"])
    flash("Clé SSH enregistrée : vos prochaines machines n'accepteront que cette clé en SSH."
          if key else "Clé SSH supprimée.", "success")
    return redirect(url_for("dashboard"))


# =============================================================================
# Tableau de bord et gestion des machines
# =============================================================================
@app.route("/dashboard")
@login_required
def dashboard():
    now = db.utc_now()
    host = public_host()
    scheme = machine_scheme()
    instances = []
    for row in db.get_user_instances(g.user["id"]):
        inst = dict(row)
        expires = db.parse_utc(inst["expires_at"])
        created = db.parse_utc(inst["created_at"])
        is_running = inst["status"] == db.STATUS_RUNNING
        inst["is_running"] = is_running
        inst["remaining_seconds"] = max(0, int((expires - now).total_seconds())) if is_running else 0
        inst["total_seconds"] = max(1, int((expires - created).total_seconds()))
        inst["expires_at_local"] = db.to_local(expires).strftime("%H:%M:%S")
        inst["expires_date_local"] = db.to_local(expires).strftime("%d/%m/%Y %H:%M")
        inst["created_at_local"] = db.to_local(created).strftime("%d/%m/%Y %H:%M")
        inst["ssh_command"] = f"ssh root@{host} -p {inst['port']}"
        inst["docker_state"] = docker_container_state(inst["container_id"]) if is_running else "-"
        inst["os_label"] = DISTROS.get(inst["os_type"], {}).get("label", inst["os_type"])
        inst["mode_label"] = MODES.get(inst["mode"], {}).get("label", inst["mode"])
        inst["is_desktop"] = bool(inst["gui_port"])
        inst["term_url"] = f"{scheme}://{host}:{inst['term_port']}/" if inst["term_port"] else None
        inst["gui_url"] = (f"{scheme}://{host}:{inst['gui_port']}/vnc.html?autoconnect=true&resize=remote"
                           if inst["gui_port"] else None)
        instances.append(inst)

    active = [i for i in instances if i["is_running"]]
    history = [i for i in instances if not i["is_running"]]
    distros_ok, modes_ok = available_choices()
    # Secret à afficher une seule fois (déposé par create/rotate, consommé ici)
    reveal = session.pop("reveal", None)
    return render_template(
        "dashboard.html",
        active_instances=active,
        history_instances=history,
        active_count=len(active),
        max_instances=MAX_INSTANCES_PER_USER,
        duration_choices=DURATION_CHOICES,
        extend_choices=EXTEND_CHOICES,
        max_duration=MAX_DURATION_MINUTES,
        distros=distros_ok,
        modes=modes_ok,
        default_os=DEFAULT_OS if DEFAULT_OS in distros_ok else next(iter(distros_ok), DEFAULT_OS),
        default_mode=DEFAULT_MODE if DEFAULT_MODE in modes_ok else next(iter(modes_ok), DEFAULT_MODE),
        ssh_host=host,
        reveal=reveal,
        ssh_public_key=g.user["ssh_public_key"] or "",
        tls_machines=bool(TLS_DIR),
    )


def reveal_secret(inst_name: str, password: str, is_desktop: bool, rotated: bool = False) -> None:
    """Dépose le mot de passe dans la session pour un affichage unique sur le tableau de bord."""
    session["reveal"] = {
        "name": inst_name,
        "password": password,
        "vnc_password": password[:8] if is_desktop else None,
        "rotated": rotated,
    }


@app.route("/instances/create", methods=["POST"])
@login_required
def create_instance():
    minutes = parse_int(request.form.get("duration"))
    if not (MIN_DURATION_MINUTES <= minutes <= MAX_DURATION_MINUTES):
        flash(f"Durée invalide : choisissez entre {MIN_DURATION_MINUTES} et "
              f"{MAX_DURATION_MINUTES} minutes.", "error")
        return redirect(url_for("dashboard"))

    os_type = request.form.get("os", DEFAULT_OS)
    mode = request.form.get("mode", DEFAULT_MODE)
    if os_type not in DISTROS or mode not in MODES:
        flash("Distribution ou mode inconnu.", "error")
        return redirect(url_for("dashboard"))
    if not image_available(os_type, mode):
        flash(f"{DISTROS[os_type]['label']} en mode {MODES[mode]['label'].lower()} n'est pas "
              f"disponible sur ce serveur (mode démonstration).", "error")
        return redirect(url_for("dashboard"))

    if db.count_active_instances(g.user["id"]) >= MAX_INSTANCES_PER_USER:
        flash(f"Quota atteint : vous ne pouvez pas louer plus de "
              f"{MAX_INSTANCES_PER_USER} machines simultanément.", "error")
        return redirect(url_for("dashboard"))

    try:
        port = choose_random_port()
        term_port = choose_random_port(exclude={port})
        gui_port = choose_random_port(exclude={port, term_port}) if MODES[mode]["gui"] else None
    except DockerError as exc:
        flash(str(exc), "error")
        return redirect(url_for("dashboard"))
    name = f"{CONTAINER_PREFIX}{g.user['username']}_{secrets.token_hex(3)}"
    root_password = generate_password()

    try:
        container_id = docker_run_container(port, name, root_password, os_type, mode,
                                            term_port, gui_port, g.user["ssh_public_key"])
    except DockerError as exc:
        log.error("Échec de création du conteneur pour '%s' : %s", g.user["username"], exc)
        flash(f"Impossible de créer la machine : {exc}", "error")
        return redirect(url_for("dashboard"))

    try:
        db.create_instance(g.user["id"], container_id, name, port, minutes,
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
    reveal_secret(name, root_password, MODES[mode]["gui"])
    flash(f"Machine « {name} » ({DISTROS[os_type]['label']}, {MODES[mode]['label'].lower()}) "
          f"louée pour {minutes} minute(s).", "success")
    return redirect(url_for("dashboard"))


@app.route("/instances/<int:instance_id>/rotate", methods=["POST"])
@login_required
def rotate_password(instance_id):
    """Génère un nouveau mot de passe root et l'applique à chaud dans la machine."""
    inst = db.get_instance(instance_id, user_id=g.user["id"])
    if inst is None:
        abort(404)
    if inst["status"] != db.STATUS_RUNNING:
        flash("Cette machine n'est plus active.", "info")
        return redirect(url_for("dashboard"))
    new_password = generate_password()
    try:
        docker_rotate_password(inst["container_id"], new_password)
    except DockerError as exc:
        log.error("Rotation impossible sur %s : %s", inst["container_name"], exc)
        flash(f"Impossible de changer le mot de passe : {exc}", "error")
        return redirect(url_for("dashboard"))
    log.info("Mot de passe régénéré : %s par '%s'", inst["container_name"], g.user["username"])
    reveal_secret(inst["container_name"], new_password, bool(inst["gui_port"]), rotated=True)
    return redirect(url_for("dashboard"))


@app.route("/instances/<int:instance_id>/delete", methods=["POST"])
@login_required
def delete_instance(instance_id):
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
@app.errorhandler(403)
def forbidden(_error):
    flash("Requête refusée (jeton de sécurité invalide ou expiré). Réessayez.", "error")
    return redirect(url_for("dashboard" if g.get("user") else "login"))


@app.errorhandler(404)
def not_found(_error):
    flash("Ressource introuvable.", "error")
    return redirect(url_for("dashboard" if g.get("user") else "login"))


# =============================================================================
# Démarrage
# =============================================================================
db.init_db()

if __name__ == "__main__":
    host = os.environ.get("INSACLOUD_HOST", "0.0.0.0")
    port = int(os.environ.get("INSACLOUD_PORT", "5000"))
    debug = os.environ.get("INSACLOUD_DEBUG", "0") == "1"
    if os.environ.get("INSACLOUD_EMBED_FAUCHEUR", "0") == "1":
        if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            import faucheur
            faucheur.start_in_background()
            log.info("Faucheur démarré en thread d'arrière-plan.")
    log.info("InsaCloud démarre sur http://%s:%s (distributions=%s, modes=%s, TLS machines=%s)",
             host, port, ", ".join(DISTROS), ", ".join(MODES), bool(TLS_DIR))
    app.run(host=host, port=port, debug=debug)
