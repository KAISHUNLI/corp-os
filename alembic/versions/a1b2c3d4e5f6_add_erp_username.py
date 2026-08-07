"""Add users.erp_username for ERP identity mapping.

Revision ID: a1b2c3d4e5f6
Revises: fbee210e88a6
Create Date: 2026-08-05 17:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "fbee210e88a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='erp_username'"
        )
    ).scalar()
    if not exists:
        op.add_column("users", sa.Column("erp_username", sa.String(length=64), nullable=True))
    # index may already exist from bootstrap
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_erp_username ON users (erp_username)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_users_erp_username")
    conn = op.get_bind()
    exists = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='erp_username'"
        )
    ).scalar()
    if exists:
        op.drop_column("users", "erp_username")
