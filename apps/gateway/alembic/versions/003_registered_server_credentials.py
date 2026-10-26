"""Add encrypted upstream credential column to registered_servers.

Revision ID: 003_server_creds
Revises: 002_core_models
Create Date: 2026-10-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003_server_creds"
down_revision: Union[str, Sequence[str], None] = "002_core_models"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "registered_servers",
        sa.Column("auth_header_ciphertext", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("registered_servers", "auth_header_ciphertext")
