"""
migrations/env.py - Point d'entrée des migrations Alembic.

Deux différences avec le modèle livré par `alembic init` :

  * l'URL de la base n'est pas lue dans alembic.ini mais dans DATABASE_URL,
    exactement comme l'application. Une migration s'applique donc forcément à
    la base que le service utilise, jamais à une autre par inadvertance ;
  * `target_metadata` pointe sur le modèle SQLAlchemy du projet, ce qui permet
    à `alembic revision --autogenerate` de comparer le schéma réel au modèle.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Le paquet de l'application est un cran au-dessus de migrations/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models import Base  # noqa: E402

config = context.config

# `configure_logger` à False : l'application appelle Alembic en cours de
# démarrage et ne veut pas qu'il reconfigure la journalisation du processus.
if (config.config_file_name is not None
        and config.attributes.get("configure_logger", True)):
    fileConfig(config.config_file_name)

# Même source de vérité que l'application
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
url = os.environ.get("DATABASE_URL") or (
    "sqlite:///" + os.environ.get("INSACLOUD_DB", os.path.join(BASE_DIR, "insacloud.db")))
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Produit le SQL sans se connecter : utile pour relire ce qui sera appliqué."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite ne sait pas modifier une colonne en place : Alembic recrée la
        # table et recopie les données. Sans cela, toute migration autre qu'un
        # simple ajout échouerait sur SQLite.
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Applique les migrations sur la base réelle."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=url.startswith("sqlite"),
            compare_type=True,          # détecte aussi les changements de type
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
