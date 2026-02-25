"""Initial schema

Revision ID: 001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
from sqlalchemy import text

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENUMS = [
    ("platform_enum",                       "('x', 'youtube', 'manual')"),
    ("content_type_enum",                   "('text', 'video')"),
    ("opinion_type_enum",                   "('prediction', 'history', 'advice', 'commentary')"),
    ("opinion_status_enum",                 "('pending', 'tracking', 'verified', 'refuted', 'expired', 'closed')"),
    ("prediction_verification_status_enum", "('pending', 'verified_true', 'verified_false', 'expired')"),
    ("assumption_level_enum",               "('none', 'low', 'medium', 'high')"),
    ("sentiment_enum",                      "('positive', 'negative', 'neutral', 'mixed')"),
    ("verification_result_enum",            "('supports', 'refutes', 'inconclusive', 'pending')"),
]

TABLES = [
    """CREATE TABLE IF NOT EXISTS bloggers (
        id              SERIAL PRIMARY KEY,
        platform        platform_enum NOT NULL,
        url             VARCHAR(2048),
        name            VARCHAR(255) NOT NULL,
        description     TEXT,
        is_active       BOOLEAN NOT NULL DEFAULT TRUE,
        last_crawled_at TIMESTAMPTZ,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_bloggers_id ON bloggers(id)",

    """CREATE TABLE IF NOT EXISTS raw_contents (
        id           SERIAL PRIMARY KEY,
        blogger_id   INTEGER NOT NULL REFERENCES bloggers(id) ON DELETE CASCADE,
        platform     VARCHAR(50) NOT NULL,
        content_type content_type_enum NOT NULL,
        raw_text     TEXT,
        video_url    VARCHAR(2048),
        transcript   TEXT,
        source_url   VARCHAR(2048),
        source_id    VARCHAR(512),
        published_at TIMESTAMPTZ,
        crawled_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        is_processed BOOLEAN NOT NULL DEFAULT FALSE
    )""",
    "CREATE INDEX IF NOT EXISTS ix_raw_contents_id ON raw_contents(id)",
    "CREATE INDEX IF NOT EXISTS ix_raw_contents_blogger_id ON raw_contents(blogger_id)",
    "CREATE INDEX IF NOT EXISTS ix_raw_contents_source_id ON raw_contents(source_id)",

    """CREATE TABLE IF NOT EXISTS opinions (
        id             SERIAL PRIMARY KEY,
        blogger_id     INTEGER NOT NULL REFERENCES bloggers(id) ON DELETE CASCADE,
        raw_content_id INTEGER REFERENCES raw_contents(id) ON DELETE SET NULL,
        text           TEXT NOT NULL,
        abstract_level INTEGER NOT NULL DEFAULT 1,
        opinion_type   opinion_type_enum NOT NULL,
        status         opinion_status_enum NOT NULL DEFAULT 'pending',
        importance     INTEGER NOT NULL DEFAULT 3,
        influence      INTEGER NOT NULL DEFAULT 3,
        domain_tags    TEXT[],
        topic_tags     TEXT[],
        language       VARCHAR(20),
        source_quote   TEXT,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_opinions_id ON opinions(id)",
    "CREATE INDEX IF NOT EXISTS ix_opinions_blogger_id ON opinions(blogger_id)",
    "CREATE INDEX IF NOT EXISTS ix_opinions_raw_content_id ON opinions(raw_content_id)",
    "CREATE INDEX IF NOT EXISTS ix_opinions_opinion_type ON opinions(opinion_type)",
    "CREATE INDEX IF NOT EXISTS ix_opinions_status ON opinions(status)",

    """CREATE TABLE IF NOT EXISTS prediction_details (
        id                    SERIAL PRIMARY KEY,
        opinion_id            INTEGER NOT NULL UNIQUE REFERENCES opinions(id) ON DELETE CASCADE,
        prediction_summary    TEXT NOT NULL,
        deadline              DATE,
        verification_status   prediction_verification_status_enum NOT NULL DEFAULT 'pending',
        evidence_links        TEXT[],
        authoritative_sources TEXT[],
        last_checked_at       TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS ix_prediction_details_id ON prediction_details(id)",

    """CREATE TABLE IF NOT EXISTS history_details (
        id                 SERIAL PRIMARY KEY,
        opinion_id         INTEGER NOT NULL UNIQUE REFERENCES opinions(id) ON DELETE CASCADE,
        claim_summary      TEXT NOT NULL,
        is_complete        BOOLEAN NOT NULL DEFAULT FALSE,
        assumption_level   assumption_level_enum NOT NULL DEFAULT 'none',
        has_assumptions    BOOLEAN NOT NULL DEFAULT FALSE,
        assumption_list    TEXT[],
        can_verify         BOOLEAN NOT NULL DEFAULT FALSE,
        verification_notes TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_history_details_id ON history_details(id)",

    """CREATE TABLE IF NOT EXISTS advice_details (
        id                 SERIAL PRIMARY KEY,
        opinion_id         INTEGER NOT NULL UNIQUE REFERENCES opinions(id) ON DELETE CASCADE,
        advice_summary     TEXT NOT NULL,
        basis              TEXT,
        rarity_score       INTEGER NOT NULL DEFAULT 3,
        importance_score   INTEGER NOT NULL DEFAULT 3,
        source_credibility TEXT,
        action_items       TEXT[]
    )""",
    "CREATE INDEX IF NOT EXISTS ix_advice_details_id ON advice_details(id)",

    """CREATE TABLE IF NOT EXISTS commentary_details (
        id                     SERIAL PRIMARY KEY,
        opinion_id             INTEGER NOT NULL UNIQUE REFERENCES opinions(id) ON DELETE CASCADE,
        sentiment              sentiment_enum NOT NULL DEFAULT 'neutral',
        target_subject         TEXT,
        public_opinion_summary TEXT,
        followup_opinions      TEXT[],
        last_tracked_at        TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS ix_commentary_details_id ON commentary_details(id)",

    """CREATE TABLE IF NOT EXISTS verification_records (
        id            SERIAL PRIMARY KEY,
        opinion_id    INTEGER NOT NULL REFERENCES opinions(id) ON DELETE CASCADE,
        check_type    VARCHAR(100) NOT NULL,
        result        verification_result_enum NOT NULL DEFAULT 'pending',
        evidence_text TEXT,
        source_url    VARCHAR(2048),
        authoritative BOOLEAN NOT NULL DEFAULT FALSE,
        checked_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS ix_verification_records_id ON verification_records(id)",
    "CREATE INDEX IF NOT EXISTS ix_verification_records_opinion_id ON verification_records(opinion_id)",
]


def upgrade() -> None:
    conn = op.get_bind()
    for name, values in ENUMS:
        conn.execute(text(
            f"DO $$ BEGIN CREATE TYPE {name} AS ENUM {values}; "
            f"EXCEPTION WHEN duplicate_object THEN NULL; END $$"
        ))
    for stmt in TABLES:
        conn.execute(text(stmt))


def downgrade() -> None:
    conn = op.get_bind()
    for tbl in ["verification_records", "commentary_details", "advice_details",
                "history_details", "prediction_details", "opinions",
                "raw_contents", "bloggers"]:
        conn.execute(text(f"DROP TABLE IF EXISTS {tbl} CASCADE"))
    for name, _ in reversed(ENUMS):
        conn.execute(text(f"DROP TYPE IF EXISTS {name}"))
