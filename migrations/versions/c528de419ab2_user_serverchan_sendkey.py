"""add encrypted per-user ServerChan SendKey storage

Revision ID: c528de419ab2
Revises: a41c8f2d7e90
Create Date: 2026-10-25 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "c528de419ab2"
down_revision = "a41c8f2d7e90"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("serverchan_sendkey", sa.String(length=512), nullable=False, server_default=""))


def downgrade():
    op.drop_column("users", "serverchan_sendkey")
