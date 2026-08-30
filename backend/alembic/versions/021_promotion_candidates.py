"""Compatibility marker for existing promotion candidates migration.

Revision ID: 021_promotion_candidates
Revises: 020_ticket_attempt_lifecycle_columns
Create Date: 2026-06-19

Some long-lived local databases were stamped at this revision by an earlier
migration file that is not present in the current tree. The current model/schema
already has promotion_candidates on those databases; keep this revision as a
no-op marker so Alembic can continue to later revisions instead of failing at
startup with "Can't locate revision identified by '021_promotion_candidates'".
"""

revision = "021_promotion_candidates"
down_revision = "020_ticket_attempt_lifecycle_columns"
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
