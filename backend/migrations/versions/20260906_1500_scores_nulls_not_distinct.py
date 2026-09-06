"""Stop baseline scores duplicating on a re-run.

Baseline rows have a NULL model_version_id. Postgres treats NULLs as distinct
in a unique constraint by default, so the natural key silently failed to
deduplicate them and every re-scored day added another set of rows.

Revision ID: b2ae91f04d7c
Revises: cce77c145b0a
"""

from alembic import op

revision = "b2ae91f04d7c"
down_revision = "cce77c145b0a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove any duplicates the old constraint let through before tightening it.
    op.execute(
        """
        DELETE FROM model_scores a
        USING model_scores b
        WHERE a.id < b.id
          AND a.model_version_id IS NOT DISTINCT FROM b.model_version_id
          AND a.estimator = b.estimator
          AND a.scored_on = b.scored_on
          AND a.horizon_hours = b.horizon_hours;
        """
    )
    op.drop_constraint("uq_score_natural_key", "model_scores", type_="unique")
    op.execute(
        """
        ALTER TABLE model_scores
        ADD CONSTRAINT uq_score_natural_key
        UNIQUE NULLS NOT DISTINCT (model_version_id, estimator, scored_on, horizon_hours);
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_score_natural_key", "model_scores", type_="unique")
    op.create_unique_constraint(
        "uq_score_natural_key",
        "model_scores",
        ["model_version_id", "estimator", "scored_on", "horizon_hours"],
    )
