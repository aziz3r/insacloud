# -*- coding: utf-8 -*-
"""
database.py - Couche d'accès à la base de données SQLite d'InsaCloud.

Ce module est partagé par le serveur web (app.py) et par le Faucheur
(faucheur.py). Il contient :
  - l'initialisation du schéma (tables `users` et `instances`) ;
  - des fonctions d'accès simples (CRUD) pour ne jamais écrire de SQL
    directement dans les routes Flask.

Conventions :
  - Toutes les dates sont stockées en UTC au format "YYYY-MM-DD HH:MM:SS",
    ce qui est exactement le format renvoyé par datetime('now') de SQLite.
    On peut donc comparer les dates directement en SQL.
  - Statuts possibles d'une instance :
        running  -> conteneur en vie, location en cours
        expired  -> durée dépassée, conteneur supprimé par le Faucheur
        stopped  -> arrêtée volontairement par l'utilisateur (ou disparue)
"""

import os
import sqlite3
from datetime import datetime, timezone

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Chemin du fichier SQLite (surchargeable par variable d'environnement)
DB_PATH = os.environ.get("INSACLOUD_DB", os.path.join(BASE_DIR, "insacloud.db"))

# Format de date compatible avec datetime('now') de SQLite
DATE_FMT = "%Y-%m-%d %H:%M:%S"

STATUS_RUNNING = "running"
STATUS_EXPIRED = "expired"
STATUS_STOPPED = "stopped"


# -----------------------------------------------------------------------------
# Utilitaires temps
# -----------------------------------------------------------------------------
def utc_now() -> datetime:
    """Retourne l'heure courante en UTC (datetime naïf, sans tzinfo)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_utc(value: str) -> datetime:
    """Convertit une chaîne de la BDD ("YYYY-MM-DD HH:MM:SS") en datetime UTC."""
    return datetime.strptime(value, DATE_FMT)


def to_local(value: datetime) -> datetime:
    """Convertit un datetime UTC naïf en heure locale de la machine (affichage)."""
    return value.replace(tzinfo=timezone.utc).astimezone()


# -----------------------------------------------------------------------------
# Connexion
# -----------------------------------------------------------------------------
def get_connection() -> sqlite3.Connection:
    """
    Ouvre une connexion SQLite.

    - row_factory = sqlite3.Row : les lignes sont accessibles par nom de colonne.
    - timeout = 10 s : attend si l'autre processus (Faucheur / Flask) écrit.
    - journal_mode = WAL : permet des lectures concurrentes pendant une écriture,
      indispensable car deux processus distincts utilisent le même fichier.
    """
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


# -----------------------------------------------------------------------------
# Initialisation du schéma
# -----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    NOT NULL UNIQUE,
    password_hash  TEXT    NOT NULL,           -- PBKDF2-SHA256 salé (werkzeug)
    ssh_public_key TEXT,                       -- clé publique SSH optionnelle
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Journal des tentatives de connexion : limitation anti-force-brute + audit
CREATE TABLE IF NOT EXISTS login_attempts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT    NOT NULL,
    ip         TEXT    NOT NULL,
    success    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_user ON login_attempts (username, created_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip   ON login_attempts (ip, created_at);

CREATE TABLE IF NOT EXISTS instances (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    container_id   TEXT    NOT NULL,           -- ID court Docker (12 caractères)
    container_name TEXT    NOT NULL,           -- nom lisible : insacloud_<user>_<rand>
    port           INTEGER NOT NULL,           -- port hôte redirigé vers le 22 du conteneur
    root_password  TEXT    NOT NULL DEFAULT '', -- CHIFFRÉ (Fernet, clé hors base) ; '' = indisponible
    os_type        TEXT    NOT NULL DEFAULT 'ubuntu',   -- distribution : ubuntu | debian | alpine
    worker         TEXT    NOT NULL DEFAULT 'local',    -- nœud Docker qui héberge le conteneur
    mode           TEXT    NOT NULL DEFAULT 'terminal', -- terminal | desktop
    term_port      INTEGER,                   -- port hôte du terminal web (ttyd)
    gui_port       INTEGER,                   -- port hôte du bureau graphique (noVNC), NULL en mode terminal
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at     TEXT    NOT NULL,           -- date de fin de location (UTC)
    status         TEXT    NOT NULL DEFAULT 'running',
    terminated_at  TEXT                        -- date de destruction effective
);

-- Index utilisé en boucle par le Faucheur : "status = running AND expires_at <= now"
CREATE INDEX IF NOT EXISTS idx_instances_status_expires
    ON instances (status, expires_at);
"""


def init_db() -> None:
    """Crée les tables si elles n'existent pas (opération idempotente)."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        # Migration douce : ajoute os_type aux bases créées avant le multi-OS
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(instances)")}
        if "os_type" not in columns:
            conn.execute("ALTER TABLE instances ADD COLUMN os_type TEXT NOT NULL DEFAULT 'ubuntu'")
        if "gui_port" not in columns:
            conn.execute("ALTER TABLE instances ADD COLUMN gui_port INTEGER")
        if "mode" not in columns:
            conn.execute("ALTER TABLE instances ADD COLUMN mode TEXT NOT NULL DEFAULT 'terminal'")
        if "term_port" not in columns:
            conn.execute("ALTER TABLE instances ADD COLUMN term_port INTEGER")
        if "worker" not in columns:
            conn.execute("ALTER TABLE instances ADD COLUMN worker TEXT NOT NULL DEFAULT 'local'")
        user_columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)")}
        if "ssh_public_key" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN ssh_public_key TEXT")
        # Sécurité : purge des mots de passe root que les anciennes versions stockaient EN CLAIR
        # (les valeurs chiffrées commencent par "gAAAA", préfixe des jetons Fernet)
        conn.execute("UPDATE instances SET root_password = '' "
                     "WHERE root_password != '' AND root_password NOT LIKE 'gAAAA%'")


# -----------------------------------------------------------------------------
# Utilisateurs
# -----------------------------------------------------------------------------
def create_user(username: str, password_hash: str):
    """
    Crée un utilisateur. Retourne son id, ou None si le nom existe déjà.
    """
    try:
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash),
            )
            return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_user_by_username(username: str):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def get_user_by_id(user_id: int):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def set_user_ssh_key(user_id: int, public_key: str) -> None:
    """Enregistre (ou efface si chaîne vide) la clé publique SSH de l'utilisateur."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET ssh_public_key = ? WHERE id = ?",
            (public_key or None, user_id),
        )


# -----------------------------------------------------------------------------
# Tentatives de connexion (anti-force-brute)
# -----------------------------------------------------------------------------
def record_login_attempt(username: str, ip: str, success: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO login_attempts (username, ip, success) VALUES (?, ?, ?)",
            (username[:64], ip[:64], 1 if success else 0),
        )


def count_recent_failures(minutes: int, username: str = None, ip: str = None) -> int:
    """Nombre d'échecs récents pour un compte et/ou une adresse IP."""
    clauses, params = ["success = 0", "created_at >= datetime('now', ?)"], [f"-{int(minutes)} minutes"]
    if username is not None:
        clauses.append("username = ?"); params.append(username)
    if ip is not None:
        clauses.append("ip = ?"); params.append(ip)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM login_attempts WHERE {' AND '.join(clauses)}", params
        ).fetchone()
        return row["n"]


def purge_login_attempts(hours: int = 24) -> None:
    """Supprime les entrées plus anciennes que `hours` (rétention courte)."""
    with get_connection() as conn:
        conn.execute("DELETE FROM login_attempts WHERE created_at < datetime('now', ?)",
                     (f"-{int(hours)} hours",))


# -----------------------------------------------------------------------------
# Instances (machines louées)
# -----------------------------------------------------------------------------
def create_instance(user_id: int, container_id: str, container_name: str,
                    port: int, duration_minutes: int,
                    os_type: str = "ubuntu", mode: str = "terminal",
                    term_port: int = None, gui_port: int = None,
                    root_password_enc: str = "", worker: str = "local") -> int:
    """
    Enregistre une nouvelle location. La date d'expiration est calculée
    côté SQLite à partir de l'heure courante UTC : now + N minutes.
    `root_password_enc` est le mot de passe root CHIFFRÉ par l'application
    (jamais en clair en base).
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO instances
                (user_id, container_id, container_name, port, root_password,
                 os_type, mode, term_port, gui_port, worker, expires_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', ?))
            """,
            (user_id, container_id, container_name, port, root_password_enc or "",
             os_type, mode, term_port, gui_port, worker, f"+{int(duration_minutes)} minutes"),
        )
        return cur.lastrowid


def set_instance_password(instance_id: int, root_password_enc: str) -> None:
    """Met à jour le mot de passe root chiffré après une rotation."""
    with get_connection() as conn:
        conn.execute("UPDATE instances SET root_password = ? WHERE id = ?",
                     (root_password_enc, instance_id))


def get_instance(instance_id: int, user_id: int = None):
    """
    Récupère une instance par id. Si user_id est fourni, on vérifie aussi
    qu'elle appartient bien à cet utilisateur (protection d'accès).
    """
    with get_connection() as conn:
        if user_id is None:
            return conn.execute(
                "SELECT * FROM instances WHERE id = ?", (instance_id,)
            ).fetchone()
        return conn.execute(
            "SELECT * FROM instances WHERE id = ? AND user_id = ?",
            (instance_id, user_id),
        ).fetchone()


def get_user_instances(user_id: int):
    """Toutes les instances d'un utilisateur (actives d'abord, puis les plus récentes)."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM instances
            WHERE user_id = ?
            ORDER BY (status = 'running') DESC, created_at DESC
            """,
            (user_id,),
        ).fetchall()


def count_active_instances(user_id: int) -> int:
    """Nombre de machines actuellement louées par l'utilisateur (quota)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM instances WHERE user_id = ? AND status = 'running'",
            (user_id,),
        ).fetchone()
        return row["n"]


def get_active_instances():
    """Toutes les instances en cours (tous utilisateurs confondus)."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM instances WHERE status = 'running'"
        ).fetchall()


def get_expired_instances():
    """
    Instances en cours dont la date de fin est dépassée.
    C'est LA requête exécutée en boucle par le Faucheur.
    """
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT instances.*, users.username
            FROM instances
            JOIN users ON users.id = instances.user_id
            WHERE instances.status = 'running'
              AND instances.expires_at <= datetime('now')
            """
        ).fetchall()


def port_in_use(port: int, worker: str = "local") -> bool:
    """Vrai si une instance active de ce nœud occupe déjà ce port hôte (SSH, terminal web ou bureau)."""
    with get_connection() as conn:
        row = conn.execute(
            """SELECT 1 FROM instances
               WHERE worker = ? AND (port = ? OR term_port = ? OR gui_port = ?) AND status = 'running' LIMIT 1""",
            (worker, port, port, port),
        ).fetchone()
        return row is not None


def count_running_per_worker() -> dict:
    """{nom_du_worker: nombre de machines actives} — sert à répartir la charge."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT worker, COUNT(*) AS n FROM instances WHERE status = 'running' GROUP BY worker"
        ).fetchall()
        return {row["worker"]: row["n"] for row in rows}


def set_instance_status(instance_id: int, status: str) -> None:
    """Change le statut d'une instance et horodate sa fin de vie."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE instances
            SET status = ?, terminated_at = datetime('now')
            WHERE id = ?
            """,
            (status, instance_id),
        )


def extend_instance(instance_id: int, minutes: int) -> None:
    """Prolonge la location de N minutes (à partir de la date de fin actuelle)."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE instances
            SET expires_at = datetime(expires_at, ?)
            WHERE id = ? AND status = 'running'
            """,
            (f"+{int(minutes)} minutes", instance_id),
        )


# -----------------------------------------------------------------------------
# Exécution directe : `python3 database.py` crée simplement la base.
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    print(f"Base de données initialisée : {DB_PATH}")
