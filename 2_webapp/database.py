"""
database.py - Accès aux données InsaCloud (SQLAlchemy).

Le schéma vit dans models.py ; ce module expose les opérations métier et rend
des dictionnaires simples, pour que l'application et le Faucheur n'aient pas à
manipuler de sessions ORM.

Moteur : SQLite par défaut (fichier unique, aucun service à installer), ou tout
autre moteur via DATABASE_URL — PostgreSQL en particulier, que le fichier
docker-compose.yml démarre à côté de l'application.

Concurrence : Flask (4 workers Gunicorn), le Faucheur et l'agent des workers
écrivent dans la même base. En SQLite, le mode WAL autorise des lectures
pendant une écriture ; c'est ce qui rend cette cohabitation possible.
"""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import (create_engine, delete, event, func, select, text,
                        update)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import (IntegrityError, OperationalError,
                            ProgrammingError)
from sqlalchemy.orm import Session, sessionmaker

from models import (DISTRIBUTION_ACTIVE, INSTANCE_EXPIRED, INSTANCE_RUNNING,
                    INSTANCE_STOPPED, RENTAL_ACTIVE, RENTAL_EXPIRED,
                    RENTAL_STOPPED, WORKER_AVAILABLE, WORKER_BUSY,
                    WORKER_OFFLINE, Base, Distribution, Instance, LoginAttempt,
                    Rental, User, Worker, utc_now)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Chemin SQLite historique, conservé pour compatibilité ; DATABASE_URL a priorité.
DB_PATH = os.environ.get("INSACLOUD_DB", os.path.join(BASE_DIR, "insacloud.db"))
DATABASE_URL = os.environ.get("DATABASE_URL") or f"sqlite:///{DB_PATH}"

# Statuts exposés tels quels au reste de l'application
STATUS_RUNNING = INSTANCE_RUNNING
STATUS_EXPIRED = INSTANCE_EXPIRED
STATUS_STOPPED = INSTANCE_STOPPED

# Un worker sans battement de cœur depuis ce délai est déclaré hors ligne.
HEARTBEAT_TIMEOUT = int(os.environ.get("INSACLOUD_HEARTBEAT_TIMEOUT", "90"))

LOCAL_WORKER = "local"     # nœud fictif du mode mono-hôte

log = logging.getLogger("insacloud.database")

def url_sans_secret(url: str = None) -> str:
    """
    Chaîne de connexion débarrassée de son mot de passe, pour les journaux.

    Une URL PostgreSQL contient le mot de passe en clair ; la journaliser telle
    quelle le déposerait dans journald, dans les journaux de conteneur et dans
    toute remontée d'erreur.
    """
    url = url or DATABASE_URL
    if "://" not in url or "@" not in url:
        return url
    schema, reste = url.split("://", 1)
    identifiants, hote = reste.rsplit("@", 1)
    utilisateur = identifiants.split(":", 1)[0]
    return f"{schema}://{utilisateur}:***@{hote}"


engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,
    connect_args={"timeout": 15, "check_same_thread": False}
    if DATABASE_URL.startswith("sqlite") else {},
)


@event.listens_for(engine, "connect")
def _configurer_sqlite(dbapi_connection, _record):
    """WAL + clés étrangères : sans cela SQLite ignore silencieusement les FK."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    curseur = dbapi_connection.cursor()
    curseur.execute("PRAGMA journal_mode=WAL")
    curseur.execute("PRAGMA foreign_keys=ON")
    curseur.execute("PRAGMA busy_timeout=15000")
    curseur.close()


SessionLocal = sessionmaker(bind=engine, future=True, expire_on_commit=False)


# =============================================================================
#  Dates
# =============================================================================
def parse_utc(value):
    """Accepte une chaîne SQLite ou un datetime, retourne toujours un datetime naïf UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if value is None:
        return None
    return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")


def to_local(value: datetime) -> datetime:
    """Convertit un datetime UTC naïf en heure locale de la machine (affichage)."""
    return value.replace(tzinfo=timezone.utc).astimezone()


# =============================================================================
#  Distributions livrées avec la plateforme
# =============================================================================
DISTRIBUTIONS_PAR_DEFAUT = [
    {"name": "ubuntu", "docker_image": "insacloud_ubuntu", "version": "22.04",
     "label": "Ubuntu 22.04", "hint": "La plus répandue, outils familiers"},
    {"name": "debian", "docker_image": "insacloud_debian", "version": "12",
     "label": "Debian 12", "hint": "Stable et sobre, base de nombreux serveurs"},
    {"name": "alpine", "docker_image": "insacloud_alpine", "version": "3.20",
     "label": "Alpine Linux 3.20", "hint": "Ultra-légère, démarre instantanément"},
]


def _marquer_schema_a_jour() -> None:
    """
    Marque une base créée par `create_all()` comme étant à la dernière révision.

    Deux mécanismes savent créer le schéma : `create_all()`, pratique en
    développement et pour les tests, et Alembic, qui fait foi en production.
    Sans ce marquage, `alembic upgrade head` tenterait de recréer des tables
    déjà présentes et échouerait. On inscrit donc la révision courante dans
    `alembic_version` : la migration suivante partira du bon point.
    """
    # Le niveau est abaissé AVANT l'import : Alembic journalise dès le
    # chargement de ses greffons, et ces lignes n'ont rien à faire dans le
    # journal de démarrage de l'application.
    journal = logging.getLogger("alembic")
    niveau = journal.level
    journal.setLevel(logging.WARNING)
    try:
        from alembic import command
        from alembic.config import Config
    except ImportError:
        journal.setLevel(niveau)
        return                                  # Alembic absent : rien à marquer

    chemin_ini = os.path.join(BASE_DIR, "alembic.ini")
    dossier = os.path.join(BASE_DIR, "migrations")
    if not (os.path.exists(chemin_ini) and os.path.isdir(dossier)):
        journal.setLevel(niveau)
        return

    with engine.connect() as connexion:
        if sa_inspect(connexion).has_table("alembic_version"):
            # La table peut exister sans contenir de révision : c'est ce que
            # laisse une migration interrompue. Dans ce cas la base n'est pas
            # réellement suivie, et il faut bel et bien la marquer.
            revision = connexion.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")).first()
            if revision is not None:
                journal.setLevel(niveau)
                return                          # déjà suivie par Alembic

    configuration = Config(chemin_ini)
    configuration.set_main_option("script_location", dossier)
    configuration.attributes["configure_logger"] = False

    try:
        command.stamp(configuration, "head")
        log.info("Base placée sous suivi Alembic.")
    except (OperationalError, ProgrammingError, IntegrityError) as erreur:
        # Un autre processus a marqué la base entre notre vérification et
        # notre écriture : le résultat voulu est atteint, on n'insiste pas.
        log.info("Marquage Alembic déjà effectué par un autre processus (%s).",
                 type(erreur).__name__)
    finally:
        journal.setLevel(niveau)


def init_db() -> None:
    """
    Crée le schéma s'il manque et garnit le catalogue des distributions.

    Appelée au démarrage par chaque processus : les quatre workers Gunicorn,
    le Faucheur, et l'agent. Ils démarrent en même temps et peuvent donc créer
    les tables simultanément — « CREATE TABLE IF NOT EXISTS » ne suffit pas,
    car deux processus peuvent passer le test avant que l'un ait créé la table.
    Les erreurs « existe déjà » sont donc tolérées : c'est exactement le
    résultat recherché, obtenu par un autre processus.
    """
    for tentative in range(5):
        try:
            Base.metadata.create_all(engine)
            break
        except (OperationalError, ProgrammingError) as erreur:
            message = str(erreur).lower()
            if "already exists" in message:
                log.info("Schéma créé simultanément par un autre processus.")
                break
            # « database is locked » : en SQLite, plusieurs processus qui
            # créent le schéma d'une base neuve se disputent le verrou
            # d'écriture. On laisse passer celui qui l'a, puis on revient.
            if "locked" not in message or tentative == 4:
                raise
            time.sleep(0.3 * (tentative + 1))

    _marquer_schema_a_jour()

    # Une transaction par distribution, et non une seule pour les trois : si
    # un autre processus insère la même ligne entre notre lecture et notre
    # écriture, seule CETTE insertion échoue, et les autres aboutissent.
    for donnees in DISTRIBUTIONS_PAR_DEFAUT:
        with SessionLocal() as session:
            try:
                existante = session.scalar(
                    select(Distribution).where(Distribution.name == donnees["name"]))
                if existante is not None:
                    continue
                session.add(Distribution(status=DISTRIBUTION_ACTIVE, **donnees))
                session.commit()
            except IntegrityError:
                session.rollback()      # insérée entre-temps : c'est le but


# =============================================================================
#  Utilisateurs
# =============================================================================
def _en_dict(objet, extra: dict = None) -> dict:
    """Transforme une ligne ORM en dictionnaire simple (clés = colonnes)."""
    if objet is None:
        return None
    resultat = {c.name: getattr(objet, c.name) for c in objet.__table__.columns}
    if extra:
        resultat.update(extra)
    return resultat


def create_user(username: str, password_hash: str, email: str = None):
    """Crée un utilisateur. Retourne son id, ou None si l'identifiant existe déjà."""
    with SessionLocal() as session:
        try:
            utilisateur = User(username=username, password_hash=password_hash,
                               email=email or None)
            session.add(utilisateur)
            session.commit()
            return utilisateur.id
        except IntegrityError:
            session.rollback()
            return None


def get_user_by_username(username: str):
    with SessionLocal() as session:
        return _en_dict(session.scalar(select(User).where(User.username == username)))


def get_user_by_id(user_id: int):
    with SessionLocal() as session:
        return _en_dict(session.get(User, user_id))


def set_user_ssh_key(user_id: int, public_key: str) -> None:
    with SessionLocal() as session:
        session.execute(update(User).where(User.id == user_id)
                        .values(ssh_public_key=public_key or None))
        session.commit()


def set_user_email(user_id: int, email: str) -> None:
    with SessionLocal() as session:
        session.execute(update(User).where(User.id == user_id)
                        .values(email=email or None))
        session.commit()


# =============================================================================
#  Tentatives de connexion (anti-force-brute)
# =============================================================================
def record_login_attempt(username: str, ip: str, success: bool) -> None:
    with SessionLocal() as session:
        session.add(LoginAttempt(username=username[:32], ip=ip[:45], success=bool(success)))
        session.commit()


def count_recent_failures(minutes: int, username: str = None, ip: str = None) -> int:
    """Échecs récents pour un compte et/ou une adresse IP, sur une fenêtre glissante."""
    depuis = utc_now() - timedelta(minutes=int(minutes))
    requete = select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.success.is_(False), LoginAttempt.created_at >= depuis)
    if username is not None:
        requete = requete.where(LoginAttempt.username == username)
    if ip is not None:
        requete = requete.where(LoginAttempt.ip == ip)
    with SessionLocal() as session:
        return session.scalar(requete) or 0


def purge_login_attempts(hours: int = 24) -> None:
    with SessionLocal() as session:
        session.execute(delete(LoginAttempt).where(
            LoginAttempt.created_at < utc_now() - timedelta(hours=int(hours))))
        session.commit()


# =============================================================================
#  Workers : enregistrement, battement de cœur, états
# =============================================================================
def register_worker(hostname: str, ip: str, cpu: int = None, memory: int = None,
                    capacity: int = None) -> dict:
    """
    Enregistre un worker, ou met à jour sa fiche s'il revient après une panne.
    Appelé par worker_agent.py sur POST /workers/register.
    """
    with SessionLocal() as session:
        worker = session.scalar(select(Worker).where(Worker.hostname == hostname))
        if worker is None:
            worker = Worker(hostname=hostname, ip=ip)
            session.add(worker)
        worker.ip = ip
        worker.cpu = cpu
        worker.memory = memory
        # Capacité : une machine « terminal » consomme 256 Mio, on garde 1 Gio au système.
        worker.capacity = capacity or (max(1, (memory - 1024) // 256) if memory else 10)
        worker.status = WORKER_AVAILABLE
        worker.last_heartbeat = utc_now()
        session.commit()
        return _en_dict(worker)


def worker_heartbeat(hostname: str, status: str = None) -> bool:
    """Enregistre un battement de cœur. Retourne False si le worker est inconnu."""
    with SessionLocal() as session:
        worker = session.scalar(select(Worker).where(Worker.hostname == hostname))
        if worker is None:
            return False
        worker.last_heartbeat = utc_now()
        if worker.status == WORKER_OFFLINE:
            worker.status = WORKER_AVAILABLE      # le nœud est revenu
        if status in (WORKER_AVAILABLE, WORKER_BUSY):
            worker.status = status
        session.commit()
        return True


def get_workers() -> list:
    """Tous les workers, avec leur charge courante."""
    with SessionLocal() as session:
        workers = session.scalars(select(Worker).order_by(Worker.hostname)).all()
        charges = dict(session.execute(
            select(Instance.worker_id, func.count())
            .where(Instance.status == INSTANCE_RUNNING)
            .group_by(Instance.worker_id)).all())
        return [_en_dict(w, {
            "running_instances": charges.get(w.id, 0),
            "reachable": w.est_joignable(HEARTBEAT_TIMEOUT),
        }) for w in workers]


def get_worker(worker_id: int) -> dict:
    with SessionLocal() as session:
        worker = session.get(Worker, worker_id)
        if worker is None:
            return None
        charge = session.scalar(
            select(func.count()).select_from(Instance)
            .where(Instance.worker_id == worker_id,
                   Instance.status == INSTANCE_RUNNING)) or 0
        return _en_dict(worker, {"running_instances": charge,
                                 "reachable": worker.est_joignable(HEARTBEAT_TIMEOUT)})


def get_worker_by_hostname(hostname: str) -> dict:
    with SessionLocal() as session:
        return _en_dict(session.scalar(select(Worker).where(Worker.hostname == hostname)))


def set_worker_status(hostname: str, status: str) -> None:
    with SessionLocal() as session:
        session.execute(update(Worker).where(Worker.hostname == hostname).values(status=status))
        session.commit()


def mark_stale_workers_offline(timeout_seconds: int = None) -> list:
    """
    Passe OFFLINE tout worker muet depuis trop longtemps.
    Retourne les noms des workers qui viennent de basculer — c'est le signal
    qui déclenche la reprise des instances (haute disponibilité).
    """
    timeout = timeout_seconds or HEARTBEAT_TIMEOUT
    limite = utc_now() - timedelta(seconds=timeout)
    bascules = []
    with SessionLocal() as session:
        for worker in session.scalars(select(Worker).where(Worker.status != WORKER_OFFLINE)):
            if worker.last_heartbeat is None or worker.last_heartbeat < limite:
                worker.status = WORKER_OFFLINE
                bascules.append(worker.hostname)
        session.commit()
    return bascules


def ensure_worker(hostname: str, ip: str = None) -> int:
    """
    Retourne l'id du worker, en le créant si besoin. Sert au mode mono-hôte
    (« local ») et aux workers déclarés par configuration plutôt que par agent.
    """
    with SessionLocal() as session:
        worker = session.scalar(select(Worker).where(Worker.hostname == hostname))
        if worker is None:
            worker = Worker(hostname=hostname, ip=ip or "127.0.0.1",
                            status=WORKER_AVAILABLE, last_heartbeat=utc_now())
            session.add(worker)
            session.commit()
        elif ip and worker.ip != ip:
            worker.ip = ip
            session.commit()
        return worker.id


def select_available_worker(prefer: list = None) -> dict:
    """
    Choisit le worker le moins chargé parmi ceux qui sont joignables et n'ont
    pas atteint leur capacité. Retourne None si aucun n'est disponible.
    """
    candidats = [w for w in get_workers()
                 if w["status"] != WORKER_OFFLINE and w["reachable"]
                 and w["running_instances"] < w["capacity"]]
    if prefer:
        filtres = [w for w in candidats if w["hostname"] in prefer]
        candidats = filtres or candidats
    if not candidats:
        return None
    return min(candidats, key=lambda w: (w["running_instances"], w["hostname"]))


# =============================================================================
#  Distributions
# =============================================================================
def get_distributions(actives_seulement: bool = True) -> list:
    requete = select(Distribution).order_by(Distribution.id)
    if actives_seulement:
        requete = requete.where(Distribution.status == DISTRIBUTION_ACTIVE)
    with SessionLocal() as session:
        return [_en_dict(d) for d in session.scalars(requete)]


def get_distribution_by_name(name: str) -> dict:
    with SessionLocal() as session:
        return _en_dict(session.scalar(select(Distribution).where(Distribution.name == name)))


def set_distribution_status(name: str, status: str) -> None:
    with SessionLocal() as session:
        session.execute(update(Distribution).where(Distribution.name == name)
                        .values(status=status))
        session.commit()


# =============================================================================
#  Instances et locations
# =============================================================================
def _instance_en_dict(instance: Instance, rental: Rental, worker: Worker,
                      distribution: Distribution, username: str = None) -> dict:
    """
    Vue « à plat » d'une location, telle que l'application et les gabarits
    l'attendent : l'instance, sa location et les noms du worker et de la distro.
    """
    donnees = _en_dict(instance)
    donnees.update({
        "port": instance.ssh_port,                 # nom historique côté interface
        "worker": worker.hostname if worker else None,
        "worker_ip": worker.ip if worker else None,
        "os_type": distribution.name if distribution else None,
        "distribution_label": distribution.label if distribution else None,
        "rental_id": rental.id if rental else None,
        "user_id": rental.user_id if rental else None,
        "start_time": rental.start_time if rental else None,
        "expires_at": rental.end_time if rental else None,
        "rental_status": rental.status if rental else None,
    })
    if username is not None:
        donnees["username"] = username
    return donnees


def _charger(session: Session, requete) -> list:
    """Exécute une requête jointe instance/location/worker/distribution."""
    lignes = session.execute(requete).all()
    return [_instance_en_dict(i, r, w, d, u) for i, r, w, d, u in lignes]


_REQUETE_BASE = (
    select(Instance, Rental, Worker, Distribution, User.username)
    .join(Rental, Rental.instance_id == Instance.id)
    .join(Worker, Worker.id == Instance.worker_id)
    .join(Distribution, Distribution.id == Instance.distribution_id)
    .join(User, User.id == Rental.user_id)
)


def create_instance(user_id: int, container_id: str, container_name: str,
                    port: int, duration_minutes: int,
                    os_type: str = "ubuntu", mode: str = "terminal",
                    term_port: int = None, gui_port: int = None,
                    root_password_enc: str = "",  # nosec B107 - jeton chiffré, pas un mot de passe
                    worker: str = LOCAL_WORKER) -> int:
    """
    Enregistre une instance ET sa location, en une transaction.
    Retourne l'id de l'instance.
    """
    with SessionLocal() as session:
        distribution = session.scalar(select(Distribution).where(Distribution.name == os_type))
        if distribution is None:
            raise ValueError(f"Distribution inconnue : {os_type}")
        noeud = session.scalar(select(Worker).where(Worker.hostname == worker))
        if noeud is None:
            session.add(Worker(hostname=worker, ip="127.0.0.1",
                               status=WORKER_AVAILABLE, last_heartbeat=utc_now()))
            session.commit()
            noeud = session.scalar(select(Worker).where(Worker.hostname == worker))

        instance = Instance(
            container_id=container_id, container_name=container_name,
            worker_id=noeud.id, distribution_id=distribution.id,
            ssh_port=port, mode=mode, term_port=term_port, gui_port=gui_port,
            root_password=root_password_enc or None, status=INSTANCE_RUNNING,
        )
        session.add(instance)
        session.flush()                      # obtient instance.id sans valider

        debut = utc_now()
        session.add(Rental(user_id=user_id, instance_id=instance.id,
                           start_time=debut,
                           end_time=debut + timedelta(minutes=int(duration_minutes)),
                           status=RENTAL_ACTIVE))
        session.commit()
        return instance.id


def set_instance_password(instance_id: int, root_password_enc: str) -> None:
    with SessionLocal() as session:
        session.execute(update(Instance).where(Instance.id == instance_id)
                        .values(root_password=root_password_enc))
        session.commit()


def get_instance(instance_id: int, user_id: int = None):
    """Une instance par id ; avec user_id, vérifie aussi qu'elle lui appartient."""
    requete = _REQUETE_BASE.where(Instance.id == instance_id)
    if user_id is not None:
        requete = requete.where(Rental.user_id == user_id)
    with SessionLocal() as session:
        resultats = _charger(session, requete)
        return resultats[0] if resultats else None


def get_user_instances(user_id: int) -> list:
    """Toutes les locations d'un utilisateur : actives d'abord, puis les plus récentes."""
    requete = (_REQUETE_BASE.where(Rental.user_id == user_id)
               .order_by((Instance.status == INSTANCE_RUNNING).desc(),
                         Instance.created_at.desc()))
    with SessionLocal() as session:
        return _charger(session, requete)


def count_active_instances(user_id: int) -> int:
    with SessionLocal() as session:
        return session.scalar(
            select(func.count()).select_from(Instance)
            .join(Rental, Rental.instance_id == Instance.id)
            .where(Rental.user_id == user_id, Instance.status == INSTANCE_RUNNING)) or 0


def get_active_instances() -> list:
    with SessionLocal() as session:
        return _charger(session, _REQUETE_BASE.where(Instance.status == INSTANCE_RUNNING))


def get_expired_instances() -> list:
    """Instances en cours dont la location est échue : la requête du Faucheur."""
    requete = _REQUETE_BASE.where(Instance.status == INSTANCE_RUNNING,
                                  Rental.end_time <= utc_now())
    with SessionLocal() as session:
        return _charger(session, requete)


def get_instances_on_worker(hostname: str, actives_seulement: bool = True) -> list:
    """Instances hébergées par un worker donné (sert à la reprise après panne)."""
    requete = _REQUETE_BASE.where(Worker.hostname == hostname)
    if actives_seulement:
        requete = requete.where(Instance.status == INSTANCE_RUNNING)
    with SessionLocal() as session:
        return _charger(session, requete)


def port_in_use(port: int, worker: str = LOCAL_WORKER) -> bool:
    """Vrai si une instance active de ce nœud occupe déjà ce port hôte."""
    with SessionLocal() as session:
        noeud = session.scalar(select(Worker).where(Worker.hostname == worker))
        if noeud is None:
            return False
        trouve = session.scalar(
            select(Instance.id).where(
                Instance.worker_id == noeud.id,
                Instance.status == INSTANCE_RUNNING,
                (Instance.ssh_port == port) | (Instance.term_port == port)
                | (Instance.gui_port == port)).limit(1))
        return trouve is not None


def count_running_per_worker() -> dict:
    """{nom_du_worker: nombre de machines actives} — sert à répartir la charge."""
    with SessionLocal() as session:
        lignes = session.execute(
            select(Worker.hostname, func.count(Instance.id))
            .join(Instance, (Instance.worker_id == Worker.id)
                  & (Instance.status == INSTANCE_RUNNING), isouter=True)
            .group_by(Worker.hostname)).all()
        return {nom: n for nom, n in lignes}


def set_instance_status(instance_id: int, status: str) -> None:
    """Change le statut d'une instance et met sa location en cohérence."""
    correspondance = {INSTANCE_EXPIRED: RENTAL_EXPIRED, INSTANCE_STOPPED: RENTAL_STOPPED,
                      INSTANCE_RUNNING: RENTAL_ACTIVE}
    with SessionLocal() as session:
        instance = session.get(Instance, instance_id)
        if instance is None:
            return
        instance.status = status
        instance.terminated_at = utc_now() if status != INSTANCE_RUNNING else None
        location = session.scalar(select(Rental).where(Rental.instance_id == instance_id))
        if location is not None:
            location.status = correspondance.get(status, location.status)
        session.commit()


def move_instance(instance_id: int, container_id: str, worker_hostname: str,
                  ssh_port: int, term_port: int = None, gui_port: int = None) -> None:
    """
    Rattache une instance à un autre worker après l'avoir recréée.
    La location n'est pas touchée : l'utilisateur garde sa durée.
    """
    with SessionLocal() as session:
        noeud = session.scalar(select(Worker).where(Worker.hostname == worker_hostname))
        if noeud is None:
            raise ValueError(f"Worker inconnu : {worker_hostname}")
        instance = session.get(Instance, instance_id)
        if instance is None:
            return
        instance.container_id = container_id
        instance.worker_id = noeud.id
        instance.ssh_port = ssh_port
        instance.term_port = term_port
        instance.gui_port = gui_port
        instance.status = INSTANCE_RUNNING
        instance.terminated_at = None
        instance.migrations = (instance.migrations or 0) + 1
        session.commit()


def extend_instance(instance_id: int, minutes: int) -> None:
    """Prolonge la location de N minutes à partir de sa date de fin actuelle."""
    with SessionLocal() as session:
        location = session.scalar(select(Rental).where(Rental.instance_id == instance_id))
        if location is None or location.status != RENTAL_ACTIVE:
            return
        location.end_time = location.end_time + timedelta(minutes=int(minutes))
        session.commit()


def get_statistics() -> dict:
    """Chiffres du tableau de supervision : parc, locations, utilisateurs."""
    with SessionLocal() as session:
        workers = session.scalars(select(Worker)).all()
        return {
            "users": session.scalar(select(func.count()).select_from(User)) or 0,
            "workers_total": len(workers),
            "workers_available": sum(1 for w in workers if w.status == WORKER_AVAILABLE),
            "workers_offline": sum(1 for w in workers if w.status == WORKER_OFFLINE),
            "instances_running": session.scalar(
                select(func.count()).select_from(Instance)
                .where(Instance.status == INSTANCE_RUNNING)) or 0,
            "instances_total": session.scalar(select(func.count()).select_from(Instance)) or 0,
            "rentals_active": session.scalar(
                select(func.count()).select_from(Rental)
                .where(Rental.status == RENTAL_ACTIVE)) or 0,
            # Taille du catalogue. Le nombre réellement louable dépend des
            # images présentes sur les nœuds : c'est /distributions qui le dit.
            "distributions_total": session.scalar(
                select(func.count()).select_from(Distribution)
                .where(Distribution.status == DISTRIBUTION_ACTIVE)) or 0,
        }


if __name__ == "__main__":
    init_db()
    print(f"Base de données initialisée : {DATABASE_URL}")
