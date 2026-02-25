"""Initial schema: bloggers, raw_contents, opinions, opinion details, verification records

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enums ---
    platform_enum = postgresql.ENUM(
        "x", "youtube", "manual", name="platform_enum", create_type=True
    )
    platform_enum.create(op.get_bind(), checkfirst=True)

    content_type_enum = postgresql.ENUM(
        "text", "video", name="content_type_enum", create_type=True
    )
    content_type_enum.create(op.get_bind(), checkfirst=True)

    opinion_type_enum = postgresql.ENUM(
        "prediction", "history", "advice", "commentary",
        name="opinion_type_enum", create_type=True
    )
    opinion_type_enum.create(op.get_bind(), checkfirst=True)

    opinion_status_enum = postgresql.ENUM(
        "pending", "tracking", "verified", "refuted", "expired", "closed",
        name="opinion_status_enum", create_type=True
    )
    opinion_status_enum.create(op.get_bind(), checkfirst=True)

    prediction_verification_status_enum = postgresql.ENUM(
        "pending", "verified_true", "verified_false", "expired",
        name="prediction_verification_status_enum", create_type=True
    )
    prediction_verification_status_enum.create(op.get_bind(), checkfirst=True)

    assumption_level_enum = postgresql.ENUM(
        "none", "low", "medium", "high",
        name="assumption_level_enum", create_type=True
    )
    assumption_level_enum.create(op.get_bind(), checkfirst=True)

    sentiment_enum = postgresql.ENUM(
        "positive", "negative", "neutral", "mixed",
        name="sentiment_enum", create_type=True
    )
    sentiment_enum.create(op.get_bind(), checkfirst=True)

    verification_result_enum = postgresql.ENUM(
        "supports", "refutes", "inconclusive", "pending",
        name="verification_result_enum", create_type=True
    )
    verification_result_enum.create(op.get_bind(), checkfirst=True)

    # --- Tables ---

    # bloggers
    op.create_table(
        "bloggers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("platform", sa.Enum("x", "youtube", "manual", name="platform_enum"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True, nullable=False),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_bloggers_id", "bloggers", ["id"])

    # raw_contents
    op.create_table(
        "raw_contents",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("blogger_id", sa.Integer(), sa.ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("content_type", sa.Enum("text", "video", name="content_type_enum"), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("video_url", sa.String(2048), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("source_id", sa.String(512), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crawled_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("is_processed", sa.Boolean(), default=False, nullable=False),
    )
    op.create_index("ix_raw_contents_id", "raw_contents", ["id"])
    op.create_index("ix_raw_contents_blogger_id", "raw_contents", ["blogger_id"])
    op.create_index("ix_raw_contents_source_id", "raw_contents", ["source_id"])

    # opinions
    op.create_table(
        "opinions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("blogger_id", sa.Integer(), sa.ForeignKey("bloggers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("raw_content_id", sa.Integer(), sa.ForeignKey("raw_contents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("abstract_level", sa.Integer(), default=1, nullable=False),
        sa.Column("opinion_type", sa.Enum("prediction", "history", "advice", "commentary", name="opinion_type_enum"), nullable=False),
        sa.Column("status", sa.Enum("pending", "tracking", "verified", "refuted", "expired", "closed", name="opinion_status_enum"), default="pending", nullable=False),
        sa.Column("importance", sa.Integer(), default=3, nullable=False),
        sa.Column("influence", sa.Integer(), default=3, nullable=False),
        sa.Column("domain_tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("topic_tags", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("language", sa.String(20), nullable=True),
        sa.Column("source_quote", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
    )
    op.create_index("ix_opinions_id", "opinions", ["id"])
    op.create_index("ix_opinions_blogger_id", "opinions", ["blogger_id"])
    op.create_index("ix_opinions_raw_content_id", "opinions", ["raw_content_id"])
    op.create_index("ix_opinions_opinion_type", "opinions", ["opinion_type"])
    op.create_index("ix_opinions_status", "opinions", ["status"])

    # prediction_details
    op.create_table(
        "prediction_details",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("opinion_id", sa.Integer(), sa.ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("prediction_summary", sa.Text(), nullable=False),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum("pending", "verified_true", "verified_false", "expired", name="prediction_verification_status_enum"),
            default="pending",
            nullable=False,
        ),
        sa.Column("evidence_links", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("authoritative_sources", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_prediction_details_id", "prediction_details", ["id"])

    # history_details
    op.create_table(
        "history_details",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("opinion_id", sa.Integer(), sa.ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("claim_summary", sa.Text(), nullable=False),
        sa.Column("is_complete", sa.Boolean(), default=False, nullable=False),
        sa.Column(
            "assumption_level",
            sa.Enum("none", "low", "medium", "high", name="assumption_level_enum"),
            default="none",
            nullable=False,
        ),
        sa.Column("has_assumptions", sa.Boolean(), default=False, nullable=False),
        sa.Column("assumption_list", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("can_verify", sa.Boolean(), default=False, nullable=False),
        sa.Column("verification_notes", sa.Text(), nullable=True),
    )
    op.create_index("ix_history_details_id", "history_details", ["id"])

    # advice_details
    op.create_table(
        "advice_details",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("opinion_id", sa.Integer(), sa.ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("advice_summary", sa.Text(), nullable=False),
        sa.Column("basis", sa.Text(), nullable=True),
        sa.Column("rarity_score", sa.Integer(), default=3, nullable=False),
        sa.Column("importance_score", sa.Integer(), default=3, nullable=False),
        sa.Column("source_credibility", sa.Text(), nullable=True),
        sa.Column("action_items", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.create_index("ix_advice_details_id", "advice_details", ["id"])

    # commentary_details
    op.create_table(
        "commentary_details",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("opinion_id", sa.Integer(), sa.ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column(
            "sentiment",
            sa.Enum("positive", "negative", "neutral", "mixed", name="sentiment_enum"),
            default="neutral",
            nullable=False,
        ),
        sa.Column("target_subject", sa.Text(), nullable=True),
        sa.Column("public_opinion_summary", sa.Text(), nullable=True),
        sa.Column("followup_opinions", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("last_tracked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_commentary_details_id", "commentary_details", ["id"])

    # verification_records
    op.create_table(
        "verification_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("opinion_id", sa.Integer(), sa.ForeignKey("opinions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_type", sa.String(100), nullable=False),
        sa.Column(
            "result",
            sa.Enum("supports", "refutes", "inconclusive", "pending", name="verification_result_enum"),
            default="pending",
            nullable=False,
        ),
        sa.Column("evidence_text", sa.Text(), nullable=True),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("authoritative", sa.Boolean(), default=False, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_verification_records_id", "verification_records", ["id"])
    op.create_index("ix_verification_records_opinion_id", "verification_records", ["opinion_id"])


def downgrade() -> None:
    op.drop_table("verification_records")
    op.drop_table("commentary_details")
    op.drop_table("advice_details")
    op.drop_table("history_details")
    op.drop_table("prediction_details")
    op.drop_table("opinions")
    op.drop_table("raw_contents")
    op.drop_table("bloggers")

    # Drop enums
    for enum_name in [
        "verification_result_enum",
        "sentiment_enum",
        "assumption_level_enum",
        "prediction_verification_status_enum",
        "opinion_status_enum",
        "opinion_type_enum",
        "content_type_enum",
        "platform_enum",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
