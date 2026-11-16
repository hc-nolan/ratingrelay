"""add reapply_target_drift to job

Revision ID: 57eb195c664d
Revises: 18fc115bfde7
Create Date: 2026-11-16 02:07:26.719592

Adds ``job.reapply_target_drift``: the relay asks for writes the target has
since dropped to be put back. Off by default, so every existing job keeps the
behaviour it had.

Autogenerate also proposed dropping the composite ``ix_syncrecord_lookup`` and
``ix_trackmapping_lookup`` indexes, because the models declare plain ``index=True``
columns instead. Those drops are not part of this change and are left out.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '57eb195c664d'
down_revision: Union[str, Sequence[str], None] = '18fc115bfde7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("job") as batch_op:
        batch_op.add_column(
            sa.Column(
                "reapply_target_drift", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("job") as batch_op:
        batch_op.drop_column("reapply_target_drift")
