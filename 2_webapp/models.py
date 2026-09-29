"""
models.py - Modèle de données InsaCloud (SQLAlchemy ORM).

Cinq entités, comme le cahier des charges les décrit :

    User          un compte utilisateur
    Distribution  une distribution Linux louable (image Docker)
    Worker        un nœud d'exécution qui héberge les conteneurs
    Instance      un conteneur réellement créé sur un worker
    Rental        la location : qui a réservé quelle instance, de quand à quand

Instance et Rental sont volontairement distinctes : la première décrit un objet
technique (un conteneur sur un nœud), la seconde un engagement commercial (un
utilisateur, une durée). Une instance peut être recréée sur un autre worker
après une panne sans que la location change.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, String,
                        Text, text)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    """Heure UTC sans fuseau : toutes les dates du projet sont stockées ainsi."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# =============================================================================
#  Utilisateurs
# =============================================================================
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    # Propre à InsaCloud : la clé publique remplace le mot de passe dans les machines
    ssh_public_key: Mapped[str] = mapped_column(Text, nullable=True)

    rentals: Mapped[list["Rental"]] = relationship(
        back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User {self.username}>"


class LoginAttempt(Base):
    """Journal des tentatives de connexion : sert au verrouillage anti-force-brute."""

    __tablename__ = "login_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (Index("idx_attempts_recent", "created_at", "username", "ip"),)


# =============================================================================
#  Catalogue des distributions
# =============================================================================
DISTRIBUTION_ACTIVE = "ACTIVE"
DISTRIBUTION_DISABLED = "DISABLED"


class Distribution(Base):
    """Une distribution louable. `docker_image` est l'image construite par Ansible."""

    __tablename__ = "distributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    docker_image: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=DISTRIBUTION_ACTIVE, nullable=False)

    # Affichage : libellé et accroche montrés sur le tableau de bord
    label: Mapped[str] = mapped_column(String(64), nullable=True)
    hint: Mapped[str] = mapped_column(String(128), nullable=True)

    instances: Mapped[list["Instance"]] = relationship(back_populates="distribution")

    def __repr__(self) -> str:
        return f"<Distribution {self.name} {self.version}>"


# =============================================================================
#  Parc de workers
# =============================================================================
WORKER_AVAILABLE = "AVAILABLE"
WORKER_BUSY = "BUSY"
WORKER_OFFLINE = "OFFLINE"


class Worker(Base):
    """
    Un nœud d'exécution. Il s'enregistre lui-même au démarrage (worker_agent.py)
    puis envoie un battement de cœur régulier ; sans nouvelles, il passe OFFLINE.
    """

    __tablename__ = "workers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hostname: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=WORKER_AVAILABLE, nullable=False)
    cpu: Mapped[int] = mapped_column(Integer, nullable=True)          # cœurs
    memory: Mapped[int] = mapped_column(Integer, nullable=True)       # Mio
    last_heartbeat: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    # Capacité maximale de conteneurs, déduite de la mémoire à l'enregistrement
    capacity: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    instances: Mapped[list["Instance"]] = relationship(back_populates="worker")

    def est_joignable(self, timeout_seconds: int) -> bool:
        """Vrai si le dernier battement de cœur est plus récent que le délai toléré."""
        if self.status == WORKER_OFFLINE or self.last_heartbeat is None:
            return False
        return (utc_now() - self.last_heartbeat) < timedelta(seconds=timeout_seconds)

    def __repr__(self) -> str:
        return f"<Worker {self.hostname} {self.status}>"


# =============================================================================
#  Instances (conteneurs) et locations
# =============================================================================
INSTANCE_RUNNING = "running"
INSTANCE_EXPIRED = "expired"
INSTANCE_STOPPED = "stopped"

RENTAL_ACTIVE = "ACTIVE"
RENTAL_EXPIRED = "EXPIRED"
RENTAL_STOPPED = "STOPPED"


class Instance(Base):
    """Un conteneur réellement créé sur un worker."""

    __tablename__ = "instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    container_id: Mapped[str] = mapped_column(String(64), nullable=False)
    container_name: Mapped[str] = mapped_column(String(64), nullable=False)
    worker_id: Mapped[int] = mapped_column(ForeignKey("workers.id"), nullable=False)
    distribution_id: Mapped[int] = mapped_column(ForeignKey("distributions.id"), nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=INSTANCE_RUNNING, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    # Propres à InsaCloud
    mode: Mapped[str] = mapped_column(String(16), default="terminal", nullable=False)
    term_port: Mapped[int] = mapped_column(Integer, nullable=True)
    gui_port: Mapped[int] = mapped_column(Integer, nullable=True)
    root_password: Mapped[str] = mapped_column(Text, nullable=True)   # chiffré (Fernet)
    terminated_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    # Nombre de fois que l'instance a été recréée après la panne d'un worker
    migrations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    worker: Mapped["Worker"] = relationship(back_populates="instances")
    distribution: Mapped["Distribution"] = relationship(back_populates="instances")
    rental: Mapped["Rental"] = relationship(
        back_populates="instance", uselist=False, cascade="all, delete-orphan")

    # Un même port ne peut servir deux fois sur un worker — mais seulement
    # tant que l'instance tourne : une fois détruite, le port est réutilisable.
    # D'où un index unique PARTIEL plutôt qu'une contrainte d'unicité simple.
    __table_args__ = (
        Index("idx_instances_status", "status"),
        Index("uq_worker_port_actif", "worker_id", "ssh_port", unique=True,
              sqlite_where=text("status = 'running'"),
              postgresql_where=text("status = 'running'")),
    )

    def __repr__(self) -> str:
        return f"<Instance {self.container_name} {self.status}>"


class Rental(Base):
    """La location : un utilisateur réserve une instance entre deux dates."""

    __tablename__ = "rentals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    instance_id: Mapped[int] = mapped_column(ForeignKey("instances.id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=RENTAL_ACTIVE, nullable=False)

    user: Mapped["User"] = relationship(back_populates="rentals")
    instance: Mapped["Instance"] = relationship(back_populates="rental")

    __table_args__ = (Index("idx_rentals_end", "status", "end_time"),)

    def __repr__(self) -> str:
        return f"<Rental user={self.user_id} instance={self.instance_id} {self.status}>"
