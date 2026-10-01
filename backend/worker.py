"""后台工人：用 SKIP LOCKED 认领 pending 读数并按判定用温度写入结论。

判定链路与偏置表提交校验、履历冻结共用 ``rules.py`` 同一套偏置与判定规则：
判定用温度 = 原文温度 + 提交时冻结的偏置，再按上限判定合格/超温。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import apply_bias, judge_temp

POLL_SECONDS = float(os.environ.get("WORKER_POLL_SECONDS", "1.0"))


def claim_one(conn):
    with conn.transaction():
        row = conn.execute(
            """
            SELECT id, probe_id, temp_c, bias_c, judged_temp_c
            FROM probe_readings
            WHERE status = 'pending'
            ORDER BY id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE probe_readings SET status = 'processing' WHERE id = %s",
            (row["id"],),
        )
        return row


def finish(conn, reading_id: int, probe_id: str, raw_temp_c: float, bias_c: float) -> None:
    # 与提交、履历、偏置校验同源：rules.apply_bias + rules.judge_temp
    judged_temp_c = apply_bias(raw_temp_c, bias_c)
    verdict, reason = judge_temp(judged_temp_c)
    with conn.transaction():
        conn.execute(
            """
            UPDATE probe_readings
            SET status = 'done', verdict = %s, reason = %s,
                judged_temp_c = %s, processed_at = now()
            WHERE id = %s
            """,
            (verdict, reason, judged_temp_c, reading_id),
        )
        # 判定结论同样落一笔只追加冻结副本，与在线更新同事务原子完成；
        # 不改写提交时的 created 副本，三值与结论都可在履历对拍。
        conn.execute(
            """
            INSERT INTO reading_history
                (reading_id, event_type, probe_id, raw_temp_c, bias_c,
                 judged_temp_c, verdict, reason, status, operator)
            VALUES (%s, 'judged', %s, %s, %s, %s, %s, %s, 'done', 'worker')
            """,
            (reading_id, probe_id, raw_temp_c, bias_c,
             judged_temp_c, verdict, reason),
        )
    conn.commit()


def run_once(conn) -> bool:
    row = claim_one(conn)
    if not row:
        return False
    try:
        finish(conn, row["id"], row["probe_id"],
               float(row["temp_c"]), float(row["bias_c"]))
    except Exception:
        conn.execute(
            "UPDATE probe_readings SET status = 'pending' WHERE id = %s",
            (row["id"],),
        )
        conn.commit()
        raise
    return True


def main() -> None:
    with connect_sync() as conn:
        ensure_schema_sync(conn)
        seed_if_empty_sync(conn)
        conn.commit()

    while True:
        try:
            with connect_sync() as conn:
                processed = run_once(conn)
        except Exception as exc:
            print(f"worker error: {exc}", flush=True)
            processed = False
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
