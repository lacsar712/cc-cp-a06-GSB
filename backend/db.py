import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import apply_bias, judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 一次建表 + 对既有卷做幂等加列（PG16 支持 ADD COLUMN IF NOT EXISTS）。
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    bias_c double precision NOT NULL DEFAULT 0,
    judged_temp_c double precision,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS bias_c double precision NOT NULL DEFAULT 0;
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS judged_temp_c double precision;
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

-- 温漂校准偏置：一个探头代号一条当前生效偏置
CREATE TABLE IF NOT EXISTS probe_biases (
    probe_id text PRIMARY KEY,
    bias_c double precision NOT NULL,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- 读数履历台账：只追加、不改写。在线单据与这里的冻结副本分叉后可逐列对拍。
CREATE TABLE IF NOT EXISTS reading_history (
    id serial PRIMARY KEY,
    reading_id integer NOT NULL REFERENCES probe_readings(id) ON DELETE CASCADE,
    event_type text NOT NULL CHECK (event_type IN ('created', 'judged', 'corrected')),
    probe_id text NOT NULL,
    raw_temp_c double precision NOT NULL,
    bias_c double precision NOT NULL,
    judged_temp_c double precision,
    verdict text,
    reason text,
    status text NOT NULL,
    operator text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_reading_history_reading ON reading_history (reading_id, id);
"""

READING_COLS = (
    "id, probe_id, temp_c, bias_c, judged_temp_c, verdict, reason, status, "
    "created_by, created_at, processed_at"
)


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


SAMPLES = [
    ("探头A01", 4.2, 0.0),
    ("探头B02", 12.5, 0.0),
]


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        for probe_id, raw_temp_c, bias_c in SAMPLES:
            judged = apply_bias(raw_temp_c, bias_c)
            verdict, reason = judge_temp(judged)
            row = await conn.fetchrow(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, bias_c, judged_temp_c, verdict, reason,
                     status, created_by, processed_at)
                VALUES ($1, $2, $3, $4, $5, $6, 'done', 'logger', now())
                RETURNING id
                """,
                probe_id,
                raw_temp_c,
                bias_c,
                judged,
                verdict,
                reason,
            )
            await conn.execute(
                """
                INSERT INTO reading_history
                    (reading_id, event_type, probe_id, raw_temp_c, bias_c,
                     judged_temp_c, verdict, reason, status, operator)
                VALUES ($1, 'created', $2, $3, $4, $5, $6, $7, 'done', 'logger')
                """,
                row["id"],
                probe_id,
                raw_temp_c,
                bias_c,
                judged,
                verdict,
                reason,
            )
        # 种子探头给一条 0 偏置，校准页一进来就有行可看
        for probe_id, _raw, _bias in SAMPLES:
            await conn.execute(
                """
                INSERT INTO probe_biases (probe_id, bias_c, updated_by, updated_at)
                VALUES ($1, 0, 'logger', now())
                ON CONFLICT (probe_id) DO NOTHING
                """,
                probe_id,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    for probe_id, raw_temp_c, bias_c in SAMPLES:
        judged = apply_bias(raw_temp_c, bias_c)
        verdict, reason = judge_temp(judged)
        rid = conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, bias_c, judged_temp_c, verdict, reason,
                 status, created_by, processed_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'done', 'logger', now())
            RETURNING id
            """,
            (probe_id, raw_temp_c, bias_c, judged, verdict, reason),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO reading_history
                (reading_id, event_type, probe_id, raw_temp_c, bias_c,
                 judged_temp_c, verdict, reason, status, operator)
            VALUES (%s, 'created', %s, %s, %s, %s, %s, %s, 'done', 'logger')
            """,
            (rid, probe_id, raw_temp_c, bias_c, judged, verdict, reason),
        )
    for probe_id, _raw, _bias in SAMPLES:
        conn.execute(
            """
            INSERT INTO probe_biases (probe_id, bias_c, updated_by, updated_at)
            VALUES (%s, 0, 'logger', now())
            ON CONFLICT (probe_id) DO NOTHING
            """,
            (probe_id,),
        )
    conn.commit()
