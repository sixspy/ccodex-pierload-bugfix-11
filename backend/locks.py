"""码头锁与争用登记（写入路径嵌入）。

抢票锁必须“先提交再写业务”：锁行以主键冲突做原子抢，抢到即提交，
后到者主键冲突只落争用痕，不得写业务。旧实现把锁与业务写在同一未提交
事务里，两个连接会同时读到“无锁”，导致同票双写完成。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth import AuthHeader, require_user, require_writer
from db import connect, now_iso, write_audit

router = APIRouter(tags=["locks"])


class LockIn(BaseModel):
    lock_key: str
    action: str = "acquire"


def log_contention(lock_key: str, actor: str, detail: str) -> None:
    """争用痕单独提交，保证后到者被拒也有据可查。"""
    with connect() as conn:
        conn.execute(
            """INSERT INTO contention_log(lock_key,actor,action,detail,created_at)
               VALUES (?,?,?,?,?)""",
            (lock_key, actor, "争用", detail, now_iso()),
        )
        conn.commit()


def try_acquire(conn, lock_key: str, holder: str) -> bool:
    now = now_iso()
    with connect() as lock_conn:
        try:
            lock_conn.execute(
                "INSERT INTO pier_locks(lock_key,holder,acquired_at) VALUES (?,?,?)",
                (lock_key, holder, now),
            )
            lock_conn.commit()
            return True
        except sqlite3.IntegrityError:
            lock_conn.rollback()
            row = lock_conn.execute(
                "SELECT holder FROM pier_locks WHERE lock_key=?", (lock_key,)
            ).fetchone()
            if row is not None and row["holder"] == holder:
                # 同一持有人可重入，不记争用。
                return True
            detail = f"持有者={row['holder']}" if row else "持有者=未知"
    log_contention(lock_key, holder, detail)
    return False


def release(conn, lock_key: str, holder: str) -> None:
    # 释放同样独立提交，避免随业务事务回滚把锁留死。
    with connect() as lock_conn:
        lock_conn.execute(
            "DELETE FROM pier_locks WHERE lock_key=? AND holder=?",
            (lock_key, holder),
        )
        lock_conn.commit()


def require_lock(conn, lock_key: str, holder: str) -> None:
    if not try_acquire(conn, lock_key, holder):
        raise HTTPException(409, "争用：后到只记争用，不写业务")


@router.get("/api/locks")
def list_locks(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pier_locks ORDER BY acquired_at DESC"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.get("/api/contention")
def list_contention(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM contention_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return {"items": [dict(r) for r in rows]}


@router.post("/api/locks/acquire")
def acquire_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        ok = try_acquire(conn, body.lock_key, user)
        write_audit(conn, user, "lock_acquire", body.lock_key, {"ok": ok})
        conn.commit()
        if not ok:
            raise HTTPException(409, "争用：未能获得锁")
        return {"ok": True, "lock_key": body.lock_key, "holder": user}


@router.post("/api/locks/release")
def release_lock(body: LockIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        release(conn, body.lock_key, user)
        write_audit(conn, user, "lock_release", body.lock_key, {})
        conn.commit()
        return {"ok": True}