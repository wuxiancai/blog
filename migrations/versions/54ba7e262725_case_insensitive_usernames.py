"""case insensitive usernames

Revision ID: 54ba7e262725
Revises: ec578ec205e3
Create Date: 2026-10-25 09:53:02.917788
"""
from alembic import op
import sqlalchemy as sa


revision = '54ba7e262725'
down_revision = 'ec578ec205e3'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(length=80), type_=sa.String(length=80, collation="NOCASE"), existing_nullable=False)
    op.add_column("articles", sa.Column("cover_attachment_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("articles", schema=None) as batch_op:
        batch_op.create_foreign_key("fk_articles_cover_attachment", "attachments", ["cover_attachment_id"], ["id"], ondelete="SET NULL")
    op.create_table("user_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100, collation="NOCASE"), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("name"))
    op.create_table("user_group_members",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["user_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "group_id"))
    op.create_table("audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target", sa.String(length=180), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"))
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_logs_action"), ["action"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_actor_id"), ["actor_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_audit_logs_created_at"), ["created_at"], unique=False)

def downgrade():
    with op.batch_alter_table("audit_logs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_logs_created_at"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_actor_id"))
        batch_op.drop_index(batch_op.f("ix_audit_logs_action"))
    op.drop_table("audit_logs")
    op.drop_table("user_group_members")
    op.drop_table("user_groups")
    with op.batch_alter_table("articles", schema=None) as batch_op:
        batch_op.drop_constraint("fk_articles_cover_attachment", type_="foreignkey")
        batch_op.drop_column("cover_attachment_id")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("username", existing_type=sa.String(length=80, collation="NOCASE"), type_=sa.String(length=80), existing_nullable=False)
