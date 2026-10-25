"""independent galleries with reader permissions

Revision ID: 32ad71c0f5e1
Revises: 9b15ef27a643
Create Date: 2026-10-25 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "32ad71c0f5e1"
down_revision = "9b15ef27a643"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("galleries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"))
    op.create_index("ix_galleries_owner_id", "galleries", ["owner_id"])
    op.create_table("gallery_readers",
        sa.Column("gallery_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["galleries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("gallery_id", "user_id"))
    op.create_table("gallery_reader_groups",
        sa.Column("gallery_id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["galleries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["group_id"], ["user_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("gallery_id", "group_id"))
    op.create_table("gallery_images",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("gallery_id", sa.Integer(), nullable=False),
        sa.Column("uploader_id", sa.Integer(), nullable=False),
        sa.Column("stored_name", sa.String(length=100), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["gallery_id"], ["galleries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("stored_name"))
    op.create_index("ix_gallery_images_gallery_id", "gallery_images", ["gallery_id"])

def downgrade():
    op.drop_index("ix_gallery_images_gallery_id", table_name="gallery_images")
    op.drop_table("gallery_images")
    op.drop_table("gallery_reader_groups")
    op.drop_table("gallery_readers")
    op.drop_index("ix_galleries_owner_id", table_name="galleries")
    op.drop_table("galleries")
