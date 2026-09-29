"""
config.py - Configuration centralisée d'InsaCloud.

Tous les réglages viennent de l'environnement, avec une valeur par défaut
utilisable telle quelle en développement. Trois profils sont fournis
(development / testing / production) ; INSACLOUD_ENV choisit lequel s'applique.

Centraliser ici évite que chaque module relise os.environ de son côté, et
supprime les dépendances circulaires entre l'application, la couche Docker et
les services métier.
"""

import os
import re
from datetime import timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Profil d'exécution -------------------------------------------------------
ENV = os.environ.get("INSACLOUD_ENV", "development").lower()


def _bool(nom: str, defaut: str = "0") -> bool:
    return os.environ.get(nom, defaut).strip().lower() in ("1", "true", "yes", "on")


# --- Réseau et ports ----------------------------------------------------------
HOST = os.environ.get("INSACLOUD_HOST", "127.0.0.1")
PORT = int(os.environ.get("INSACLOUD_PORT", "5000"))

# Plage de ports hôte dans laquelle on tire les ports publiés (SSH, terminal, bureau)
PORT_MIN = int(os.environ.get("INSACLOUD_PORT_MIN", "8000"))
PORT_MAX = int(os.environ.get("INSACLOUD_PORT_MAX", "9000"))

# --- Règles métier ------------------------------------------------------------
MAX_INSTANCES_PER_USER = int(os.environ.get("INSACLOUD_MAX_INSTANCES", "3"))
MIN_DURATION_MINUTES = 1
MAX_DURATION_MINUTES = int(os.environ.get("INSACLOUD_MAX_DURATION", "120"))
DURATION_CHOICES = [5, 10, 15, 30, 60]
EXTEND_CHOICES = [5, 10, 30]

# --- Anti-force-brute ---------------------------------------------------------
LOGIN_WINDOW_MINUTES = int(os.environ.get("INSACLOUD_LOGIN_WINDOW", "15"))
LOGIN_MAX_FAILURES_USER = int(os.environ.get("INSACLOUD_LOGIN_MAX_USER", "5"))
LOGIN_MAX_FAILURES_IP = int(os.environ.get("INSACLOUD_LOGIN_MAX_IP", "20"))

# --- Coffre à secrets ---------------------------------------------------------
VAULT_WINDOW_MINUTES = int(os.environ.get("INSACLOUD_VAULT_WINDOW", "5"))

# --- Politique de mot de passe ------------------------------------------------
PASSWORD_MIN_LENGTH = int(os.environ.get("INSACLOUD_PASSWORD_MIN", "10"))
COMMON_PASSWORDS = {"password", "motdepasse", "123456789", "1234567890", "azertyuiop",
                    "qwertyuiop", "insacloud", "administrator", "iloveyou12"}

# --- Ressources des machines louées -------------------------------------------
CONTAINER_MEMORY = os.environ.get("INSACLOUD_CONTAINER_MEMORY", "256m")
GUI_CONTAINER_MEMORY = os.environ.get("INSACLOUD_GUI_MEMORY", "1g")
# Réservation : minimum garanti en cas de contention. Le cours impose de définir
# toujours une réservation ET une limite (ni gaspillage, ni monopolisation).
CONTAINER_RESERVATION = os.environ.get("INSACLOUD_CONTAINER_RESERVATION", "128m")
GUI_CONTAINER_RESERVATION = os.environ.get("INSACLOUD_GUI_RESERVATION", "512m")
CONTAINER_CPUS = os.environ.get("INSACLOUD_CONTAINER_CPUS", "1")
CONTAINER_PIDS_LIMIT = os.environ.get("INSACLOUD_CONTAINER_PIDS", "512")
TERM_CONTAINER_PORT = 7681   # ttyd dans toutes les images
GUI_CONTAINER_PORT = 6080    # noVNC dans les images "bureau"
# Identité du déploiement. Deux plateformes InsaCloud peuvent partager le même
# démon Docker (un poste de développement et une pile compose, par exemple) :
# chacune n'a le droit de toucher qu'aux machines portant SON nom de cluster.
CLUSTER = os.environ.get("INSACLOUD_CLUSTER", "default")
CONTAINER_PREFIX = "insacloud_"
DOCKER_TIMEOUT = int(os.environ.get("INSACLOUD_DOCKER_TIMEOUT", "60"))

# --- TLS et proxy -------------------------------------------------------------
# Certificat monté dans les machines (terminal web + noVNC en HTTPS) : vide = HTTP
TLS_DIR = os.environ.get("INSACLOUD_TLS_DIR", "")
HTTPS = _bool("INSACLOUD_HTTPS")
BEHIND_PROXY = _bool("INSACLOUD_BEHIND_PROXY")
SSH_HOST_OVERRIDE = os.environ.get("INSACLOUD_SSH_HOST")

# --- Haute disponibilité ------------------------------------------------------
# Délai sans battement de cœur au-delà duquel un worker est déclaré hors ligne
HEARTBEAT_TIMEOUT = int(os.environ.get("INSACLOUD_HEARTBEAT_TIMEOUT", "90"))
HEARTBEAT_INTERVAL = int(os.environ.get("INSACLOUD_HEARTBEAT_INTERVAL", "30"))
# Recréer automatiquement sur un autre worker les instances d'un nœud tombé
AUTO_RECOVERY = _bool("INSACLOUD_AUTO_RECOVERY", "1")

# --- Validation des entrées ---------------------------------------------------
USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,31}$")   # compatible noms Docker
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
SSH_PUBKEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com)"
    r" [A-Za-z0-9+/]+=*( [^\r\n]{0,128})?$"
)

# --- Images réellement présentes sur le serveur (renseigné par Ansible) -------
AVAILABLE_IMAGES = {x.strip() for x in
                    os.environ.get("INSACLOUD_AVAILABLE_IMAGES", "").split(",") if x.strip()}

# --- Modes d'accès ------------------------------------------------------------
MODES = {
    "terminal": {"label": "Terminal", "hint": "SSH + terminal dans le navigateur",
                 "gui": False, "memory": CONTAINER_MEMORY, "reservation": CONTAINER_RESERVATION},
    "desktop":  {"label": "Bureau graphique", "hint": "XFCE et Firefox dans le navigateur, + SSH",
                 "gui": True, "memory": GUI_CONTAINER_MEMORY, "reservation": GUI_CONTAINER_RESERVATION},
}
DEFAULT_OS = os.environ.get("INSACLOUD_DEFAULT_OS", "ubuntu")
DEFAULT_MODE = os.environ.get("INSACLOUD_DEFAULT_MODE", "terminal")


# =============================================================================
#  Profils Flask : development / testing / production
# =============================================================================
class BaseConfig:
    """Réglages communs. Les trois profils n'en modifient qu'une poignée."""

    SESSION_COOKIE_HTTPONLY = True          # inaccessible à JavaScript
    SESSION_COOKIE_SAMESITE = "Strict"      # jamais envoyé depuis un autre site
    SESSION_COOKIE_SECURE = HTTPS           # HTTPS uniquement quand le site est en TLS
    SESSION_COOKIE_NAME = "insacloud_session"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=2)
    MAX_CONTENT_LENGTH = 16 * 1024          # aucune requête légitime ne dépasse 16 Ko
    JSON_SORT_KEYS = False
    DEBUG = False
    TESTING = False


class DevelopmentConfig(BaseConfig):
    """Poste de développement : rechargement à chaud, pas de TLS exigé."""

    DEBUG = _bool("INSACLOUD_DEBUG")
    SESSION_COOKIE_SECURE = False


class TestingConfig(BaseConfig):
    """Exécution des tests : base jetable, pas de cookie sécurisé à imposer."""

    TESTING = True
    SESSION_COOKIE_SECURE = False
    WTF_CSRF_ENABLED = True                 # la protection CSRF reste testée


class ProductionConfig(BaseConfig):
    """Derrière nginx : cookies Secure, HSTS, aucun mode debug possible."""

    DEBUG = False
    SESSION_COOKIE_SECURE = True


PROFILS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config():
    """Classe de configuration correspondant à INSACLOUD_ENV."""
    return PROFILS.get(ENV, DevelopmentConfig)
