"""
app.py - Serveur web Flask d'InsaCloud (mini-fournisseur de Cloud).

Fonctionnalités :
  - Inscription / connexion (mots de passe hachés, anti-force-brute, CSRF).
  - Location d'une machine (conteneur Docker) : 3 distributions × 2 modes
    (terminal ou bureau graphique), pour N minutes.
  - Tableau de bord : commande SSH, terminal web, bureau, temps restant,
    état réel du conteneur, prolongation, rotation du mot de passe, destruction.

Sécurité (voir aussi README, section « Sécurité ») :
  - le mot de passe root d'une machine est généré, affiché une fois à la
    création, puis conservé CHIFFRÉ (Fernet, clé hors base) ; il n'est
    réaffiché qu'après re-authentification de l'utilisateur (« coffre »,
    ouvert 5 minutes) ; il peut être régénéré à chaud ;
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
import hashlib
import hmac
import logging
import os
import secrets
import sys
import time

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import database as db
import services
import workers as wk
from api import api as api_blueprint
from config import (BEHIND_PROXY, COMMON_PASSWORDS, DEFAULT_MODE, DEFAULT_OS,
                    DURATION_CHOICES, EMAIL_RE, EXTEND_CHOICES, HTTPS,
                    LOGIN_MAX_FAILURES_IP, LOGIN_MAX_FAILURES_USER,
                    LOGIN_WINDOW_MINUTES, MAX_DURATION_MINUTES,
                    MAX_INSTANCES_PER_USER, MODES,
                    PASSWORD_MIN_LENGTH, SSH_HOST_OVERRIDE, SSH_PUBKEY_RE,
                    TLS_DIR, USERNAME_RE, VAULT_WINDOW_MINUTES, get_config)
from crypto import decrypt_secret, load_secret_key
from docker_ops import docker_container_state

# =============================================================================
# Configuration
# =============================================================================
# La configuration, les secrets et les appels Docker vivent désormais dans
# config.py, crypto.py et docker_ops.py : voir les imports en tête de fichier.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("insacloud")


app = Flask(__name__)
# Profil choisi par INSACLOUD_ENV : development, testing ou production.
app.config.from_object(get_config())
app.config["SECRET_KEY"] = load_secret_key()

# L'API JSON partage l'application et sa session : mêmes règles d'accès,
# mêmes en-têtes de sécurité, une seule logique métier derrière.
app.register_blueprint(api_blueprint)

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


@functools.lru_cache(maxsize=64)
def _static_fingerprint(filename: str) -> str:
    """Empreinte courte du contenu d'un fichier statique (invalide le cache navigateur à chaque changement)."""
    try:
        with open(os.path.join(app.static_folder, filename), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:10]
    except OSError:
        return "0"


def static_url(filename: str) -> str:
    return url_for("static", filename=filename, v=_static_fingerprint(filename))


app.jinja_env.globals["static_url"] = static_url


@app.before_request
def security_before_request():
    """Charge l'utilisateur, vérifie le jeton CSRF sur toute requête modifiante."""
    user_id = session.get("user_id")
    g.user = db.get_user_by_id(user_id) if user_id else None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Les routes d'agent (workers) s'authentifient par un jeton d'en-tête,
        # jamais par un cookie : une attaque CSRF, qui repose sur l'envoi
        # automatique du cookie par le navigateur, n'a pas de prise sur elles.
        vue = app.view_functions.get(request.endpoint)
        if getattr(vue, "_csrf_exempt", False):
            return None
        # Une requête au type « application/json » ne peut pas provenir d'un
        # formulaire HTML : les formulaires ne savent émettre que urlencoded,
        # multipart ou text/plain. Une requête JSON inter-origines déclenche un
        # contrôle préalable CORS auquel ce serveur ne répond pas. Combiné au
        # cookie SameSite=Strict, cela exclut l'attaque CSRF ; le jeton de
        # formulaire reste exigé partout ailleurs.
        if request.is_json:
            return None
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


def vault_remaining_seconds() -> int:
    """Secondes restantes d'ouverture du coffre (0 = verrouillé)."""
    until = session.get("vault_until", 0)
    return max(0, int(until - time.time()))


# =============================================================================
# Couche Docker (appels subprocess)
# =============================================================================
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
def veut_json() -> bool:
    """
    Vrai si le client attend du JSON plutôt qu'une page.

    Permet à /register, /login et /logout de servir à la fois le formulaire du
    site et l'API décrite au cahier des charges, sans dupliquer les chemins.
    """
    if request.is_json:
        return True
    # « Accept: */* » — ce qu'envoient curl et les navigateurs par défaut — ne
    # doit PAS être lu comme une demande de JSON, sans quoi un formulaire HTML
    # recevrait une réponse JSON. Seul un client qui accepte le JSON et refuse
    # le HTML est traité comme un client d'API.
    accepte = request.accept_mimetypes
    return accepte.accept_json and not accepte.accept_html


def champs(*noms):
    """Lit des champs indifféremment dans un corps JSON ou un formulaire."""
    source = request.get_json(silent=True) if request.is_json else None
    source = source if isinstance(source, dict) else request.form
    return [(source.get(nom) or "") for nom in noms]


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
        username, password = champs("username", "password")
        username = username.strip()[:32]
        ip = client_ip()

        # Anti-force-brute : verrouillage temporaire par compte et par adresse IP
        if (db.count_recent_failures(LOGIN_WINDOW_MINUTES, username=username) >= LOGIN_MAX_FAILURES_USER
                or db.count_recent_failures(LOGIN_WINDOW_MINUTES, ip=ip) >= LOGIN_MAX_FAILURES_IP):
            log.warning("Connexion verrouillée : user=%s ip=%s", username, ip)
            message = f"Trop de tentatives. Réessayez dans {LOGIN_WINDOW_MINUTES} minutes."
            if veut_json():
                return jsonify(error=message), 429
            flash(message, "error")
            return render_template("login.html", active_tab="login"), 429

        user = db.get_user_by_username(username)
        # check_password_hash est en temps constant ; on le fait même si le compte n'existe pas
        ok = user is not None and check_password_hash(user["password_hash"], password)
        if not ok:
            check_password_hash(generate_password_hash("x"), password) if user is None else None
            db.record_login_attempt(username, ip, False)
            log.warning("Échec de connexion : user=%s ip=%s", username, ip)
            if veut_json():
                return jsonify(error="Identifiant ou mot de passe incorrect."), 401
            flash("Identifiant ou mot de passe incorrect.", "error")
            return render_template("login.html", active_tab="login"), 401

        db.record_login_attempt(username, ip, True)
        db.purge_login_attempts(24)
        session.clear()                       # nouvelle session (anti-fixation)
        session.permanent = True
        session["user_id"] = user["id"]
        log.info("Connexion : user=%s ip=%s", username, ip)
        if veut_json():
            return jsonify(id=user["id"], username=user["username"],
                           email=user.get("email"), message="Connecté.")
        flash(f"Bienvenue, {username} !", "success")
        return redirect(url_for("dashboard"))

    return render_template("login.html", active_tab="login")


@app.route("/register", methods=["POST"])
def register():
    """Création de compte. Répond en JSON si le client le demande."""
    username, password, confirm, email = champs("username", "password", "confirm", "email")
    username, email = username.strip(), email.strip()

    def refus(message, code=400):
        if veut_json():
            return jsonify(error=message), code
        flash(message, "error")
        return render_template("login.html", active_tab="register"), code

    if not USERNAME_RE.match(username):
        return refus("Identifiant invalide : 3 à 32 caractères (lettres, chiffres, . _ -), "
                     "commençant par une lettre ou un chiffre.")
    if email and not EMAIL_RE.match(email):
        return refus("Adresse électronique invalide.")
    error = password_policy_error(password, username)
    if error:
        return refus(error)
    # En JSON, la confirmation est facultative : c'est une garde d'interface.
    if not veut_json() and password != confirm:
        return refus("Les deux mots de passe ne correspondent pas.")

    user_id = db.create_user(username, generate_password_hash(password), email or None)
    if user_id is None:
        return refus("Cet identifiant ou cette adresse est déjà utilisé.", 409)

    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    log.info("Inscription : user=%s ip=%s", username, client_ip())
    if veut_json():
        return jsonify(id=user_id, username=username, email=email or None,
                       message="Compte créé."), 201
    flash("Compte créé avec succès. Bienvenue sur InsaCloud !", "success")
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    if veut_json():
        return jsonify(status="logged_out")
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
# Coffre : re-authentification pour afficher les accès des machines
# =============================================================================
@app.route("/vault/unlock", methods=["POST"])
@login_required
def vault_unlock():
    """Ouvre le coffre pour VAULT_WINDOW_MINUTES après vérification du mot de passe du compte."""
    ip = client_ip()
    username = g.user["username"]
    if db.count_recent_failures(LOGIN_WINDOW_MINUTES, username=username) >= LOGIN_MAX_FAILURES_USER:
        flash(f"Trop de tentatives. Réessayez dans {LOGIN_WINDOW_MINUTES} minutes.", "error")
        return redirect(url_for("dashboard"))
    if not check_password_hash(g.user["password_hash"], request.form.get("password", "")):
        db.record_login_attempt(username, ip, False)
        log.warning("Coffre : mot de passe refusé user=%s ip=%s", username, ip)
        flash("Mot de passe du compte incorrect.", "error")
        return redirect(url_for("dashboard"))
    session["vault_until"] = time.time() + VAULT_WINDOW_MINUTES * 60
    log.info("Coffre ouvert : user=%s ip=%s (%s min)", username, ip, VAULT_WINDOW_MINUTES)
    flash(f"Accès visibles pendant {VAULT_WINDOW_MINUTES} minutes.", "success")
    return redirect(url_for("dashboard"))


@app.route("/vault/lock", methods=["POST"])
@login_required
def vault_lock():
    session.pop("vault_until", None)
    flash("Accès masqués.", "info")
    return redirect(url_for("dashboard"))


# =============================================================================
# Tableau de bord et gestion des machines
# =============================================================================
@app.route("/dashboard")
@login_required
def dashboard():
    now = db.utc_now()
    scheme = machine_scheme()
    catalogue = services.distributions_disponibles()
    vault_open = vault_remaining_seconds() > 0
    instances = []
    for row in db.get_user_instances(g.user["id"]):
        inst = dict(row)
        # Les liens pointent vers le nœud qui héberge la machine (ou vers ce serveur en mode local)
        host = wk.worker_host(inst["worker"]) or public_host()
        inst["worker_host"] = host
        # Le mot de passe chiffré ne quitte jamais le serveur ; en clair seulement si le coffre est ouvert
        secret = decrypt_secret(inst.pop("root_password", "")) if (vault_open and row["status"] == db.STATUS_RUNNING) else None
        inst["root_password"] = secret
        inst["vnc_password"] = secret[:8] if secret else None
        inst["has_secret"] = bool(row["root_password"])
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
        inst["docker_state"] = docker_container_state(inst["container_id"], inst["worker"]) if is_running else "-"
        inst["os_label"] = catalogue.get(inst["os_type"], {}).get("label", inst["os_type"])
        inst["mode_label"] = MODES.get(inst["mode"], {}).get("label", inst["mode"])
        inst["is_desktop"] = bool(inst["gui_port"])
        inst["term_url"] = f"{scheme}://{host}:{inst['term_port']}/" if inst["term_port"] else None
        inst["gui_url"] = (f"{scheme}://{host}:{inst['gui_port']}/vnc.html?autoconnect=true&resize=remote"
                           if inst["gui_port"] else None)
        instances.append(inst)

    active = [i for i in instances if i["is_running"]]
    history = [i for i in instances if not i["is_running"]]
    distros_ok = catalogue
    modes_ok = services.modes_disponibles(catalogue)
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
        ssh_host=public_host(),
        workers=wk.WORKERS,
        reveal=reveal,
        ssh_public_key=g.user["ssh_public_key"] or "",
        tls_machines=bool(TLS_DIR),
        vault_open=vault_open,
        vault_remaining=vault_remaining_seconds(),
        vault_window=VAULT_WINDOW_MINUTES,
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
    """
    Location depuis le formulaire du tableau de bord.

    Toute la logique est dans services.louer() : la route ne fait que traduire
    le formulaire en arguments, puis le résultat en message et redirection.
    POST /rent, le chemin du cahier des charges, est servi par l'API JSON et
    emprunte exactement le même chemin métier.
    """
    resultat = services.louer(
        dict(g.user),
        parse_int(request.form.get("duration")),
        request.form.get("os", DEFAULT_OS),
        request.form.get("mode", DEFAULT_MODE),
    )
    if not resultat.ok:
        flash(resultat.message, "error")
        return redirect(url_for("dashboard"))

    reveal_secret(resultat.donnees["name"], resultat.donnees["password"],
                  resultat.donnees["is_desktop"])
    flash(resultat.message, "success")
    return redirect(url_for("dashboard"))


@app.route("/instances/<int:instance_id>/rotate", methods=["POST"])
@login_required
def rotate_password(instance_id):
    """Génère un nouveau mot de passe root et l'applique à chaud dans la machine."""
    resultat = services.regenerer_mot_de_passe(instance_id, g.user["id"])
    if not resultat.ok:
        if resultat.code == 404:
            abort(404)
        flash(resultat.message, "error")
        return redirect(url_for("dashboard"))
    reveal_secret(resultat.donnees["name"], resultat.donnees["password"],
                  resultat.donnees["is_desktop"], rotated=True)
    return redirect(url_for("dashboard"))


@app.route("/instances/<int:instance_id>/delete", methods=["POST"])
@login_required
def delete_instance(instance_id):
    """
    Rend la machine avant l'échéance, depuis le tableau de bord.
    L'équivalent JSON est POST /instances/<id>/stop, servi par l'API.
    """
    resultat = services.arreter(instance_id, g.user["id"])
    if not resultat.ok:
        if resultat.code == 404:
            abort(404)
        flash(resultat.message, "error")
        return redirect(url_for("dashboard"))
    flash(resultat.message, "success")
    return redirect(url_for("dashboard"))


@app.route("/instances/<int:instance_id>/extend", methods=["POST"])
@login_required
def extend_instance(instance_id):
    """Prolonge la location en cours."""
    resultat = services.prolonger(instance_id, g.user["id"],
                                  parse_int(request.form.get("minutes")))
    if not resultat.ok:
        if resultat.code == 404:
            abort(404)
        flash(resultat.message, "error")
        return redirect(url_for("dashboard"))
    flash(resultat.message, "success")
    return redirect(url_for("dashboard"))


# =============================================================================
# Gestion d'erreurs
# =============================================================================
@app.errorhandler(403)
def forbidden(_error):
    message = "Requête refusée (jeton de sécurité invalide ou expiré)."
    if veut_json():
        return jsonify(error=message), 403
    flash(message + " Réessayez.", "error")
    return redirect(url_for("dashboard" if g.get("user") else "login"))


@app.errorhandler(404)
def not_found(_error):
    if veut_json():
        return jsonify(error="Ressource introuvable."), 404
    flash("Ressource introuvable.", "error")
    return redirect(url_for("dashboard" if g.get("user") else "login"))


# =============================================================================
# Démarrage
# =============================================================================
db.init_db()

if __name__ == "__main__":
    # Serveur de développement : écoute locale par défaut. En production,
    # Gunicorn est lancé par systemd et nginx assure seul l'exposition TLS.
    host = os.environ.get("INSACLOUD_HOST", "127.0.0.1")
    port = int(os.environ.get("INSACLOUD_PORT", "5000"))
    debug = os.environ.get("INSACLOUD_DEBUG", "0") == "1"
    if os.environ.get("INSACLOUD_EMBED_FAUCHEUR", "0") == "1":
        if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
            import faucheur
            faucheur.start_in_background()
            log.info("Faucheur démarré en thread d'arrière-plan.")
    log.info("InsaCloud démarre sur http://%s:%s (profil=%s, distributions=%s, modes=%s, "
             "TLS machines=%s, nœuds=%s)",
             host, port, os.environ.get("INSACLOUD_ENV", "development"),
             ", ".join(services.distributions_disponibles()), ", ".join(MODES),
             bool(TLS_DIR), ", ".join(wk.WORKERS))
    app.run(host=host, port=port, debug=debug)
