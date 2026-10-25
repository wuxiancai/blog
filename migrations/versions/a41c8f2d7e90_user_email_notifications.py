"""add user email for scheduled article notifications

Revision ID: a41c8f2d7e90
Revises: 32ad71c0f5e1
Create Date: 2026-10-25 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "a41c8f2d7e90"
down_revision = "32ad71c0f5e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email", sa.String(length=320), nullable=False, server_default=""))


def downgrade():
    op.drop_column("users", "email")
