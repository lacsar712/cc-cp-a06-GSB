"""后台工人：用 SKIP LOCKED 认领 pending 读数，按提交时冻结的判定温度写入结论。

判定用温度在提交瞬间已由在线服务按当时偏置算好并冻结进 probe_readings 与
reading_ledger。这里只读取冻结值走 rules.judge_temp —— 与偏置校验、履历共用同一套
rules，绝不重新读当前偏置，否则事后改正偏置会改写历史判定。
在线单据与冻结副本在同一事务内一起落结论；冻结副本的原文/偏置/判定温度永不修改。
"""

import os
import time

from db import connect_sync, ensure_schema_sync, seed_if_empty_sync
from rules import judge_temp

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
        # 冻结副本状态随之流转，数字列仍永不修改。
        conn.execute(
            "UPDATE reading_ledger SET status = 'processing' WHERE reading_id = %s",
            (row["id"],),
        )
        return row


def finish(conn, row) -> None:
    reading_id = row["id"]
    # 判定链路与提交/履历共用同一套 rules，输入是冻结的判定温度。
    judged_temp_c = float(row["judged_temp_c"])
    verdict, reason = judge_temp(judged_temp_c)
    with conn.transaction():
        conn.execute(
            """
            UPDATE probe_readings
            SET status = 'done', verdict = %s, reason = %s, processed_at = now()
            WHERE id = %s
            """,
            (verdict, reason, reading_id),
        )
        # 冻结副本只补结论与状态，数字旧值（temp_c/bias_c/judged_temp_c）绝不动。
        conn.execute(
            """
            UPDATE reading_ledger
            SET status = 'done', verdict = %s, reason = %s, processed_at = now()
            WHERE reading_id = %s
            """,
            (verdict, reason, reading_id),
        )
    conn.commit()


def run_once(conn) -> bool:
    row = claim_one(conn)
    if not row:
        return False
    try:
        finish(conn, row)
    except Exception:
        conn.execute(
            "UPDATE probe_readings SET status = 'pending' WHERE id = %s",
            (row["id"],),
        )
        conn.execute(
            "UPDATE reading_ledger SET status = 'pending' WHERE reading_id = %s",
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
