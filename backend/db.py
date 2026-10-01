import os

import asyncpg
import psycopg
from psycopg.rows import dict_row

from rules import apply_bias, judge_temp

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://app:app@localhost:54397/coldchain"
)

# 注意：偏置闭区间的唯一来源是 rules.py；此处 CHECK 用具体数值做数据库兜底，
# 与 rules.BIAS_MIN / BIAS_MAX 保持一致。
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS probe_readings (
    id serial PRIMARY KEY,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL DEFAULT 'pending',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    bias_c double precision NOT NULL DEFAULT 0,
    judged_temp_c double precision NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_probe_readings_status ON probe_readings (status, id);

-- 当前温漂校准偏置（单行单例，id 恒为 1）
CREATE TABLE IF NOT EXISTS probe_bias (
    id smallint PRIMARY KEY DEFAULT 1,
    bias_c double precision NOT NULL DEFAULT 0,
    updated_by text NOT NULL DEFAULT 'logger',
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_bias_range CHECK (bias_c >= -10 AND bias_c <= 10),
    CONSTRAINT chk_bias_singleton CHECK (id = 1)
);
INSERT INTO probe_bias (id, bias_c, updated_by, updated_at)
VALUES (1, 0, 'logger', now())
ON CONFLICT (id) DO NOTHING;

-- 校准履历：偏置每次改动落一笔，只追加，不改旧值
CREATE TABLE IF NOT EXISTS bias_ledger (
    id serial PRIMARY KEY,
    old_bias_c double precision,
    new_bias_c double precision NOT NULL,
    updated_by text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);

-- 读数履历：提交瞬间的冻结副本（原文温度 / 当时偏置 / 判定用温度）。
-- 在线单据可被事后改正，这里的旧值永不动，两者分叉后可对拍。
CREATE TABLE IF NOT EXISTS reading_ledger (
    id serial PRIMARY KEY,
    reading_id integer NOT NULL,
    probe_id text NOT NULL,
    temp_c double precision NOT NULL,
    bias_c double precision NOT NULL,
    judged_temp_c double precision NOT NULL,
    verdict text,
    reason text,
    status text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz
);
CREATE INDEX IF NOT EXISTS idx_reading_ledger_reading ON reading_ledger (reading_id);
"""

# 旧库（A06 基线）没有偏置/判定温度两列，幂等补列并回填历史行。
_MIGRATION_SQL = """
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS bias_c double precision NOT NULL DEFAULT 0;
ALTER TABLE probe_readings ADD COLUMN IF NOT EXISTS judged_temp_c double precision NOT NULL DEFAULT 0;
UPDATE probe_readings
SET judged_temp_c = temp_c + bias_c
WHERE judged_temp_c = 0 AND temp_c <> 0;
"""


def connect_sync():
    return psycopg.connect(DSN, row_factory=dict_row)


def ensure_schema_sync(conn) -> None:
    conn.execute(SCHEMA_SQL)
    conn.execute(_MIGRATION_SQL)


async def create_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(DSN, min_size=1, max_size=5)


async def ensure_schema_async(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
        await conn.execute(_MIGRATION_SQL)


async def seed_if_empty(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT COUNT(*) FROM probe_readings")
        if n and n > 0:
            return
        samples = [
            ("探头A01", 4.2),
            ("探头B02", 12.5),
        ]
        for probe_id, temp_c in samples:
            verdict, reason = judge_temp(temp_c)
            reading_id = await conn.fetchval(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, bias_c, judged_temp_c,
                     verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, 0, $2, $3, $4, 'done', 'logger', now())
                RETURNING id
                """,
                probe_id,
                temp_c,
                verdict,
                reason,
            )
            await conn.execute(
                """
                INSERT INTO reading_ledger
                    (reading_id, probe_id, temp_c, bias_c, judged_temp_c,
                     verdict, reason, status, created_by, processed_at)
                VALUES ($1, $2, $3, 0, $3, $4, $5, 'done', 'logger', now())
                """,
                reading_id,
                probe_id,
                temp_c,
                verdict,
                reason,
            )


def seed_if_empty_sync(conn) -> None:
    row = conn.execute("SELECT COUNT(*) AS n FROM probe_readings").fetchone()
    if row["n"] > 0:
        return
    samples = [
        ("探头A01", 4.2),
        ("探头B02", 12.5),
    ]
    for probe_id, temp_c in samples:
        verdict, reason = judge_temp(apply_bias(temp_c, 0))
        reading_id = conn.execute(
            """
            INSERT INTO probe_readings
                (probe_id, temp_c, bias_c, judged_temp_c,
                 verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, 0, %s, %s, %s, 'done', 'logger', now())
            RETURNING id
            """,
            (probe_id, temp_c, temp_c, verdict, reason),
        ).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO reading_ledger
                (reading_id, probe_id, temp_c, bias_c, judged_temp_c,
                 verdict, reason, status, created_by, processed_at)
            VALUES (%s, %s, %s, 0, %s, %s, %s, 'done', 'logger', now())
            """,
            (reading_id, probe_id, temp_c, temp_c, verdict, reason),
        )
    conn.commit()
