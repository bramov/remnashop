from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0041"
down_revision: Union[str, None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TYPE IF EXISTS platega_autopayment_status")
    op.execute(
        """
        CREATE TYPE platega_autopayment_status AS ENUM (
            'PENDING', 'ACTIVE', 'PAYMENT_FAILED', 'CANCELED', 'REPLACED'
        )
        """
    )
    op.create_table(
        "platega_autopayments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="platega_autopayment_status", create_type=False),
            nullable=False,
        ),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column(
            "purchase_type",
            postgresql.ENUM(name="purchase_type", create_type=False),
            nullable=False,
        ),
        sa.Column("pricing", postgresql.JSONB(), nullable=False),
        sa.Column(
            "currency",
            postgresql.ENUM(name="currency", create_type=False),
            nullable=False,
        ),
        sa.Column("plan_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("gateway_display_name", sa.String(), nullable=True),
        sa.Column("replaces_subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("next_charge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_charge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("timezone('UTC', now())"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subscription_id"),
    )
    op.create_index(
        "ix_platega_autopayments_subscription_id",
        "platega_autopayments",
        ["subscription_id"],
        unique=True,
    )
    op.create_index(
        "ix_platega_autopayments_user_id",
        "platega_autopayments",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_platega_autopayments_status",
        "platega_autopayments",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_platega_autopayments_status", table_name="platega_autopayments")
    op.drop_index("ix_platega_autopayments_user_id", table_name="platega_autopayments")
    op.drop_index(
        "ix_platega_autopayments_subscription_id", table_name="platega_autopayments"
    )
    op.drop_table("platega_autopayments")
    op.execute("DROP TYPE IF EXISTS platega_autopayment_status")
