"""Compatibility marker for the rejected Phase 6B prototype.

Revision ID: 24d778704bb6
Revises: b6e3f4a5c6d7
Create Date: 2026-09-01 15:00:00.000000

Some installations applied an unpublished Phase 6B prototype and now report
this revision.  The following migration normalizes that prototype to the
canonical schema.  This marker deliberately performs no DDL: fresh databases
first receive the canonical Phase 6B migration, while prototype databases can
continue from their already-recorded revision without an unsafe stamp.
"""


revision = "24d778704bb6"
down_revision = "b6e3f4a5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
