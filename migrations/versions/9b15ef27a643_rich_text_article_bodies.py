"""support rich text article bodies while preserving markdown posts

Revision ID: 9b15ef27a643
Revises: 7a2d6b31c4ef
Create Date: 2026-10-26 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "9b15ef27a643"
down_revision = "7a2d6b31c4ef"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("articles", sa.Column("body_format", sa.String(length=20), nullable=False, server_default="markdown"))

def downgrade():
    op.drop_column("articles", "body_format")
