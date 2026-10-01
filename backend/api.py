import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import create_pool, ensure_schema_async, seed_if_empty
from rules import apply_bias, judge_temp, validate_bias

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _auth_header(request: web.Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return None


def _decode_user(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def require_user(request: web.Request) -> dict:
    user = _decode_user(_auth_header(request))
    if not user:
        raise web.HTTPUnauthorized(text=json.dumps({"detail": "未登录"}, ensure_ascii=False), content_type="application/json")
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可提交读数"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _reading_out(r) -> dict:
    return {
        "id": r["id"],
        "probe_id": r["probe_id"],
        "temp_c": r["temp_c"],
        "bias_c": r["bias_c"],
        "judged_temp_c": r["judged_temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "created_by": r["created_by"],
        "created_at": _iso(r["created_at"]),
        "processed_at": _iso(r["processed_at"]),
    }


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"status": "ok", "service": "coldchain-probe-desk"})


async def login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "用户名或密码错误"}, ensure_ascii=False),
            content_type="application/json",
        )
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp},
        SECRET,
        algorithm="HS256",
    )
    return web.json_response(
        {"access_token": token, "username": username, "role": user["role"]}
    )


async def list_readings(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT id, probe_id, temp_c, bias_c, judged_temp_c,
               verdict, reason, status, created_by, created_at, processed_at
        FROM probe_readings
        ORDER BY id DESC
        """
    )
    return web.json_response([_reading_out(r) for r in rows])


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "探头编号不能为空"}, ensure_ascii=False),
            content_type="application/json",
        )
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc
    if temp_c != temp_c or temp_c in (float("inf"), float("-inf")):
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        )

    pool: asyncpg.Pool = request.app["pool"]
    # 单事务：读当前偏置 → 算判定温度 → 入队在线单据 → 落笔冻结副本履历。
    # 任一失败整体回滚，绝不会只入队而无履历。
    async with pool.acquire() as conn:
        async with conn.transaction():
            brow = await conn.fetchrow(
                "SELECT bias_c FROM probe_bias WHERE id = 1 FOR UPDATE"
            )
            bias_c = float(brow["bias_c"])
            judged_temp_c = apply_bias(temp_c, bias_c)
            row = await conn.fetchrow(
                """
                INSERT INTO probe_readings
                    (probe_id, temp_c, bias_c, judged_temp_c,
                     status, created_by, created_at)
                VALUES ($1, $2, $3, $4, 'pending', $5, now())
                RETURNING id, probe_id, temp_c, bias_c, judged_temp_c,
                          verdict, reason, status, created_by, created_at, processed_at
                """,
                probe_id,
                temp_c,
                bias_c,
                judged_temp_c,
                user["username"],
            )
            await conn.execute(
                """
                INSERT INTO reading_ledger
                    (reading_id, probe_id, temp_c, bias_c, judged_temp_c,
                     verdict, reason, status, created_by, created_at)
                VALUES ($1, $2, $3, $4, $5, NULL, NULL, 'pending', $6, now())
                """,
                row["id"],
                probe_id,
                temp_c,
                bias_c,
                judged_temp_c,
                user["username"],
            )

    out = _reading_out(row)
    out["message"] = (
        f"已入队：原文 {temp_c:g}℃ ＋ 偏置 {bias_c:g}℃ ＝ 判定 {judged_temp_c:g}℃，"
        "后台工人将认领并判定"
    )
    return web.json_response(out, status=201)


async def get_bias(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    r = await pool.fetchrow(
        "SELECT bias_c, updated_by, updated_at FROM probe_bias WHERE id = 1"
    )
    return web.json_response(
        {
            "bias_c": float(r["bias_c"]),
            "updated_by": r["updated_by"],
            "updated_at": _iso(r["updated_at"]),
        }
    )


async def set_bias(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    # 与判定链路共用 rules.validate_bias 同一套闭区间与措辞，
    # 网页与直连拿到的 detail 完全一致。
    try:
        bias_c = validate_bias(body.get("bias_c"))
    except ValueError as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": str(exc)}, ensure_ascii=False),
            content_type="application/json",
        ) from exc

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            old = await conn.fetchrow(
                "SELECT bias_c FROM probe_bias WHERE id = 1 FOR UPDATE"
            )
            await conn.execute(
                """
                UPDATE probe_bias
                SET bias_c = $1, updated_by = $2, updated_at = now()
                WHERE id = 1
                """,
                bias_c,
                user["username"],
            )
            await conn.execute(
                """
                INSERT INTO bias_ledger (old_bias_c, new_bias_c, updated_by, changed_at)
                VALUES ($1, $2, $3, now())
                """,
                float(old["bias_c"]),
                bias_c,
                user["username"],
            )
            r = await conn.fetchrow(
                "SELECT bias_c, updated_by, updated_at FROM probe_bias WHERE id = 1"
            )
    return web.json_response(
        {
            "bias_c": float(r["bias_c"]),
            "updated_by": r["updated_by"],
            "updated_at": _iso(r["updated_at"]),
            "message": f"偏置已设为 {bias_c:g}℃",
        }
    )


async def correct_reading(request: web.Request) -> web.Response:
    """事后改正在线单据数字。

    只更新在线单据 probe_readings：改正原文温度后，用该单据提交时冻结的偏置
    （仍走 rules 同一套 apply_bias / judge_temp）重算判定温度与结论。
    冻结副本 reading_ledger 一个字都不改 —— 在线单据与履历由此分叉、可对拍。
    """
    user = require_writer(request)
    try:
        reading_id = int(request.match_info["id"])
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "单据编号非法"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    try:
        temp_c = float(body.get("temp_c"))
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        ) from exc
    if temp_c != temp_c or temp_c in (float("inf"), float("-inf")):
        raise web.HTTPBadRequest(
            text=json.dumps({"detail": "温度必须是数字"}, ensure_ascii=False),
            content_type="application/json",
        )

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, bias_c FROM probe_readings WHERE id = $1 FOR UPDATE",
                reading_id,
            )
            if not row:
                raise web.HTTPNotFound(
                    text=json.dumps({"detail": "单据不存在"}, ensure_ascii=False),
                    content_type="application/json",
                )
            bias_c = float(row["bias_c"])
            judged_temp_c = apply_bias(temp_c, bias_c)
            verdict, reason = judge_temp(judged_temp_c)
            row = await conn.fetchrow(
                """
                UPDATE probe_readings
                SET temp_c = $1, judged_temp_c = $2,
                    verdict = $3, reason = $4,
                    status = 'done', processed_at = now()
                WHERE id = $5
                RETURNING id, probe_id, temp_c, bias_c, judged_temp_c,
                          verdict, reason, status, created_by, created_at, processed_at
                """,
                temp_c,
                judged_temp_c,
                verdict,
                reason,
                reading_id,
            )
            # 注意：刻意不写 reading_ledger，冻结副本保留提交时旧值。
    out = _reading_out(row)
    out["message"] = (
        f"单据 {reading_id} 已改正：原文 {temp_c:g}℃ ＋ 偏置 {bias_c:g}℃ "
        f"＝ 判定 {judged_temp_c:g}℃（{verdict}）；履历冻结副本旧值未动"
    )
    return web.json_response(out)


async def list_history(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    bias = await pool.fetchrow(
        "SELECT bias_c, updated_by, updated_at FROM probe_bias WHERE id = 1"
    )
    bias_rows = await pool.fetch(
        """
        SELECT id, old_bias_c, new_bias_c, updated_by, changed_at
        FROM bias_ledger
        ORDER BY id DESC
        """
    )
    reading_rows = await pool.fetch(
        """
        SELECT id, reading_id, probe_id, temp_c, bias_c, judged_temp_c,
               verdict, reason, status, created_by, created_at, processed_at
        FROM reading_ledger
        ORDER BY id DESC
        """
    )
    return web.json_response(
        {
            "bias": {
                "bias_c": float(bias["bias_c"]),
                "updated_by": bias["updated_by"],
                "updated_at": _iso(bias["updated_at"]),
            },
            "bias_ledger": [
                {
                    "id": r["id"],
                    "old_bias_c": r["old_bias_c"],
                    "new_bias_c": r["new_bias_c"],
                    "updated_by": r["updated_by"],
                    "changed_at": _iso(r["changed_at"]),
                }
                for r in bias_rows
            ],
            "reading_ledger": [
                {
                    "id": r["id"],
                    "reading_id": r["reading_id"],
                    "probe_id": r["probe_id"],
                    "temp_c": r["temp_c"],
                    "bias_c": r["bias_c"],
                    "judged_temp_c": r["judged_temp_c"],
                    "verdict": r["verdict"],
                    "reason": r["reason"],
                    "status": r["status"],
                    "created_by": r["created_by"],
                    "created_at": _iso(r["created_at"]),
                    "processed_at": _iso(r["processed_at"]),
                }
                for r in reading_rows
            ],
        }
    )


async def on_startup(app: web.Application) -> None:
    pool = await create_pool()
    app["pool"] = pool
    await ensure_schema_async(pool)
    await seed_if_empty(pool)


async def on_cleanup(app: web.Application) -> None:
    pool: asyncpg.Pool = app.get("pool")
    if pool:
        await pool.close()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", health)
    app.router.add_post("/api/auth/login", login)
    app.router.add_get("/api/readings", list_readings)
    app.router.add_post("/api/readings", create_reading)
    app.router.add_patch("/api/readings/{id}", correct_reading)
    app.router.add_get("/api/bias", get_bias)
    app.router.add_post("/api/bias", set_bias)
    app.router.add_get("/api/history", list_history)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
