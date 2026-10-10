"""archives du travail des utilisateurs

Identifiant : f9e8113a54a6
Précédente  : d8c810a4813f
Créée le    : 2026-10-06 22:21:52.663898
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# Identifiants de révision utilisés par Alembic
revision: str = 'f9e8113a54a6'
down_revision: str | Sequence[str] | None = 'd8c810a4813f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Applique la migration."""
    # Sur une base déjà en service, init_db() (create_all) a pu créer la table
    # avant que la migration ne tourne : on ne doit pas échouer dans ce cas.
    if sa.inspect(op.get_bind()).has_table('archives'):
        return
    op.create_table('archives',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('instance_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('path', sa.String(length=255), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('sha256', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['instance_id'], ['instances.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('archives', schema=None) as batch_op:
        batch_op.create_index('idx_archives_expiry', ['expires_at'], unique=False)
        batch_op.create_index('idx_archives_user', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    """Revient à l'état précédent."""
    with op.batch_alter_table('archives', schema=None) as batch_op:
        batch_op.drop_index('idx_archives_user')
        batch_op.drop_index('idx_archives_expiry')

    op.drop_table('archives')
