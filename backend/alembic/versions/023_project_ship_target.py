"""Per-project ship target: AgentHub-only (default) vs GitHub publish.

Revision ID: 023_project_ship_target
Revises: 022_drop_wave_num
Create Date: 2026-09-28

Existing projects default to agenthub (no GitHub release PRs unless opted in).
"""

from alembic import op
import sqlalchemy as sa


revision = "023_project_ship_target"
down_revision = "022_drop_wave_num"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "projects",
        sa.Column("ship_target", sa.String(length=20), nullable=False, server_default="agenthub"),
    )
    op.execute("UPDATE projects SET ship_target = 'agenthub' WHERE ship_target IS NULL")


def downgrade():
    op.drop_column("projects", "ship_target")
