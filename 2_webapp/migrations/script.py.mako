"""${message}

Identifiant : ${up_revision}
Précédente  : ${down_revision if down_revision else "aucune (schéma initial)"}
Créée le    : ${create_date}
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

# Identifiants de révision utilisés par Alembic
revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    """Applique la migration."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Revient à l'état précédent."""
    ${downgrades if downgrades else "pass"}
