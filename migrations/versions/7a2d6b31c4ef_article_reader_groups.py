"""allow articles to be shared with user groups

Revision ID: 7a2d6b31c4ef
Revises: 54ba7e262725
Create Date: 2026-10-25 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "7a2d6b31c4ef"
down_revision = "54ba7e262725"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "article_reader_groups",
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["user_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id", "group_id"),
    )

def downgrade():
    op.drop_table("article_reader_groups")
