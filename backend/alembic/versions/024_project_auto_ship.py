"""Per-project automatic shipping after validated attempts complete.

Revision ID: 024_project_auto_ship
Revises: 023_project_ship_target
Create Date: 2026-10-02

Existing projects default to auto_ship off.
"""

from alembic import op
import sqlalchemy as sa


revision = "024_project_auto_ship"
down_revision = "023_project_ship_target"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "projects",
        sa.Column("auto_ship", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE projects SET auto_ship = false WHERE auto_ship IS NULL")


def downgrade():
    op.drop_column("projects", "auto_ship")
