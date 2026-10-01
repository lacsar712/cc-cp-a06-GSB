import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from aiohttp import web
from passlib.context import CryptContext

from db import READING_COLS, create_pool, ensure_schema_async, seed_if_empty
from rules import (
    BiasOutOfRange,
    apply_bias,
    judge_temp,
    validate_bias,
)

SECRET = os.environ.get("JWT_SECRET", "coldchain-probe-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = {
    "logger": {"role": "writer", "password_hash": pwd.hash("log123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def _json_error(detail: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(
        text=json.dumps({"detail": detail}, ensure_ascii=False),
        content_type="application/json",
    )


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
        raise web.HTTPUnauthorized(
            text=json.dumps({"detail": "未登录"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def require_writer(request: web.Request) -> dict:
    user = require_user(request)
    if user["role"] != "writer":
        raise web.HTTPForbidden(
            text=json.dumps({"detail": "仅记录员可写"}, ensure_ascii=False),
            content_type="application/json",
        )
    return user


def _parse_float(body: dict, key: str, nan_msg: str) -> float:
    try:
        value = float(body.get(key))
    except (TypeError, ValueError) as exc:
        raise _json_error(nan_msg) from exc
    return value


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
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "processed_at": r["processed_at"].isoformat() if r["processed_at"] else None,
    }


def _history_out(r) -> dict:
    return {
        "id": r["id"],
        "reading_id": r["reading_id"],
        "event_type": r["event_type"],
        "probe_id": r["probe_id"],
        "raw_temp_c": r["raw_temp_c"],
        "bias_c": r["bias_c"],
        "judged_temp_c": r["judged_temp_c"],
        "verdict": r["verdict"],
        "reason": r["reason"],
        "status": r["status"],
        "operator": r["operator"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
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
    rows = await pool.fetch(f"SELECT {READING_COLS} FROM probe_readings ORDER BY id DESC")
    return web.json_response([_reading_out(r) for r in rows])


async def create_reading(request: web.Request) -> web.Response:
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise _json_error("探头编号不能为空")
    raw_temp_c = _parse_float(body, "temp_c", "温度必须是数字")

    pool: asyncpg.Pool = request.app["pool"]
    # 入队与履历落笔同一事务：任何一步失败整体回滚，绝不允许只入队无履历。
    async with pool.acquire() as conn, conn.transaction():
        bias_row = await conn.fetchrow(
            "SELECT bias_c FROM probe_biases WHERE probe_id = $1", probe_id
        )
        # 落库偏置都是经 validate_bias 校验过的；未配置的探头按 0 偏置处理
        bias_c = float(bias_row["bias_c"]) if bias_row else 0.0
        judged_temp_c = apply_bias(raw_temp_c, bias_c)

        row = await conn.fetchrow(
            f"""
            INSERT INTO probe_readings
                (probe_id, temp_c, bias_c, judged_temp_c, status, created_by, created_at)
            VALUES ($1, $2, $3, $4, 'pending', $5, now())
            RETURNING {READING_COLS}
            """,
            probe_id,
            raw_temp_c,
            bias_c,
            judged_temp_c,
            user["username"],
        )
        # 提交即冻结：原文温度、偏置、判定用温度一并写入履历
        await conn.execute(
            """
            INSERT INTO reading_history
                (reading_id, event_type, probe_id, raw_temp_c, bias_c,
                 judged_temp_c, verdict, reason, status, operator)
            VALUES ($1, 'created', $2, $3, $4, $5, NULL, NULL, 'pending', $6)
            """,
            row["id"],
            probe_id,
            raw_temp_c,
            bias_c,
            judged_temp_c,
            user["username"],
        )

    payload = _reading_out(row)
    payload["message"] = "已入队，后台工人将认领并判定"
    return web.json_response(payload, status=201)


async def correct_reading(request: web.Request) -> web.Response:
    """事后改正在线单据数字：只改在线行并追加 corrected 事件，旧履历不动。"""
    user = require_writer(request)
    try:
        reading_id = int(request.match_info["id"])
    except ValueError as exc:
        raise _json_error("读数编号必须是整数") from exc
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    new_raw = _parse_float(body, "temp_c", "温度必须是数字")

    pool: asyncpg.Pool = request.app["pool"]
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"SELECT {READING_COLS} FROM probe_readings WHERE id = $1 FOR UPDATE",
            reading_id,
        )
        if not row:
            raise web.HTTPNotFound(
                text=json.dumps({"detail": "读数不存在"}, ensure_ascii=False),
                content_type="application/json",
            )
        # 用提交时冻结的偏置重新判定，不回溯套用后来改过的新偏置
        bias_c = float(row["bias_c"])
        judged = apply_bias(new_raw, bias_c)
        verdict, reason = judge_temp(judged)
        updated = await conn.fetchrow(
            f"""
            UPDATE probe_readings
            SET temp_c = $2, judged_temp_c = $3, verdict = $4, reason = $5,
                status = 'done', processed_at = now()
            WHERE id = $1
            RETURNING {READING_COLS}
            """,
            reading_id,
            new_raw,
            judged,
            verdict,
            reason,
        )
        # 冻结一份改正后的副本；旧 created 行保持原样，在线单据与履历就此分叉
        await conn.execute(
            """
            INSERT INTO reading_history
                (reading_id, event_type, probe_id, raw_temp_c, bias_c,
                 judged_temp_c, verdict, reason, status, operator)
            VALUES ($1, 'corrected', $2, $3, $4, $5, $6, $7, 'done', $8)
            """,
            reading_id,
            row["probe_id"],
            new_raw,
            bias_c,
            judged,
            verdict,
            reason,
            user["username"],
        )
    return web.json_response(_reading_out(updated))


async def list_biases(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    rows = await pool.fetch(
        """
        SELECT probe_id, bias_c, updated_by, updated_at
        FROM probe_biases ORDER BY probe_id
        """
    )
    return web.json_response(
        [
            {
                "probe_id": r["probe_id"],
                "bias_c": r["bias_c"],
                "updated_by": r["updated_by"],
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ]
    )


async def put_bias(request: web.Request) -> web.Response:
    """记录员设置探头温漂偏置。越界由 rules.validate_bias 唯一校验，网页/直连同措辞。"""
    user = require_writer(request)
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise web.HTTPBadRequest(text="invalid json") from exc
    probe_id = str(body.get("probe_id", "")).strip()
    if not probe_id:
        raise _json_error("探头编号不能为空")
    bias_c = _parse_float(body, "bias_c", "偏置必须是数字")
    try:
        validate_bias(bias_c)
    except BiasOutOfRange as exc:
        # 网页与直连拿到的是同一句服务端措辞
        raise _json_error(str(exc)) from exc

    pool: asyncpg.Pool = request.app["pool"]
    row = await pool.fetchrow(
        """
        INSERT INTO probe_biases (probe_id, bias_c, updated_by, updated_at)
        VALUES ($1, $2, $3, now())
        ON CONFLICT (probe_id) DO UPDATE
        SET bias_c = EXCLUDED.bias_c,
            updated_by = EXCLUDED.updated_by,
            updated_at = now()
        RETURNING probe_id, bias_c, updated_by, updated_at
        """,
        probe_id,
        bias_c,
        user["username"],
    )
    return web.json_response(
        {
            "probe_id": row["probe_id"],
            "bias_c": row["bias_c"],
            "updated_by": row["updated_by"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            "message": "偏置已保存",
        }
    )


async def list_history(request: web.Request) -> web.Response:
    require_user(request)
    pool: asyncpg.Pool = request.app["pool"]
    reading_id = request.query.get("reading_id")
    if reading_id:
        try:
            rid = int(reading_id)
        except ValueError:
            raise _json_error("reading_id 必须是整数")
        rows = await pool.fetch(
            "SELECT * FROM reading_history WHERE reading_id = $1 ORDER BY id", rid
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM reading_history ORDER BY id DESC LIMIT 200"
        )
    return web.json_response([_history_out(r) for r in rows])


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
    app.router.add_get("/api/biases", list_biases)
    app.router.add_put("/api/biases", put_bias)
    app.router.add_get("/api/history", list_history)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=8000)
