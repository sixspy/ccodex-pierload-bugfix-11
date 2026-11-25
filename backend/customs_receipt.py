"""海关空桩回执：完成口径唯一来源。

口径（当班封死，不许折中）：
- 回执禁完：``customs_stub_jobs.receipt_ref`` 为空即不得写正式完成，
  正式册（bl_manifests.customs_status='已完成'）零改。
- 完成只影子：无回执时最多记影子待回执（shadow_*），影子不得当结案。
- 回执到达后在同一事务内原子晋升：回执列、任务行、正式册一起落库。

放行 / 对账 / 提单 / 总览 / 空桩五处必须读本模块的同一状态，不得各自推断。
"""
from __future__ import annotations

import sqlite3

from db import now_iso, write_audit

BL_UNFILED = "未申报"
JOB_QUEUED = "已入队"
SHADOW_PENDING = "影子待回执"
OFFICIAL_DONE = "已完成"

DEVIATION_ACTION = "customs_receipt_deviation"


def latest_job(conn: sqlite3.Connection, bl_code: str):
    return conn.execute(
        """SELECT * FROM customs_stub_jobs WHERE bl_code=? ORDER BY id DESC LIMIT 1""",
        (bl_code,),
    ).fetchone()


def receipt_state(conn: sqlite3.Connection, bl_code: str) -> dict:
    """只读空桩回执列，给出五页共用的回执口径。

    正式库存回执号为正式完成的唯一凭据；任务行回执列用于定位与展示。
    """
    bl = conn.execute(
        "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
    ).fetchone()
    job = latest_job(conn, bl_code)
    job_ref = (job["receipt_ref"] if job else None) or ""
    bl_ref = (bl["customs_receipt_ref"] if bl else None) or ""
    receipt_ref = bl_ref if bl_ref.strip() else job_ref
    receipt_ok = bool(receipt_ref.strip())
    if bl is None:
        return {
            "bl_code": bl_code,
            "job_id": None,
            "receipt_ref": "",
            "receipt_ok": False,
            "receipt_missing": True,
            "official": False,
            "shadow": False,
            "status": BL_UNFILED,
            "reason": "提单不存在",
        }
    official = bl["customs_status"] == OFFICIAL_DONE and receipt_ok
    shadow = bool((job["shadow_completed_at"] if job else None) or bl["customs_shadow_at"])
    if official:
        status = OFFICIAL_DONE
        reason = "回执已到，正式完成"
    elif shadow:
        status = SHADOW_PENDING
        reason = "回执缺失：仅影子完成，不得结案"
    elif job is not None:
        status = JOB_QUEUED
        reason = "回执缺失：空桩仅入队"
    else:
        status = BL_UNFILED
        reason = "未申报海关空桩"
    return {
        "bl_code": bl_code,
        "job_id": int(job["id"]) if job else None,
        "receipt_ref": receipt_ref,
        "receipt_ok": receipt_ok,
        "receipt_missing": not receipt_ok,
        "official": official,
        "shadow": shadow,
        "status": status,
        "reason": reason,
    }


def require_official(conn: sqlite3.Connection, bl_code: str) -> dict:
    """完成口专用：读回执列，缺回执直接判非正式（不抛错，交调用方记偏航）。"""
    return receipt_state(conn, bl_code)


def mark_shadow_completion(
    conn: sqlite3.Connection, bl_code: str, actor: str, source: str
) -> dict:
    """无回执：只落影子，正式册与任务行完成态零改，并记偏航说明。"""
    now = now_iso()
    state = receipt_state(conn, bl_code)
    conn.execute(
        """UPDATE customs_stub_jobs
           SET status=?, shadow_completed_at=COALESCE(shadow_completed_at,?)
           WHERE bl_code=?""",
        (SHADOW_PENDING, now, bl_code),
    )
    conn.execute(
        """UPDATE bl_manifests
           SET customs_status=?, customs_shadow_at=COALESCE(customs_shadow_at,?)
           WHERE bl_code=? AND customs_status<>?""",
        (SHADOW_PENDING, now, bl_code, OFFICIAL_DONE),
    )
    write_audit(
        conn,
        actor,
        DEVIATION_ACTION,
        bl_code,
        {
            "source": source,
            "receipt_missing": True,
            "job_id": state["job_id"],
            "rule": "回执禁完：无回执不得写正式完成",
        },
    )
    return receipt_state(conn, bl_code)


def mark_official_completion(
    conn: sqlite3.Connection, bl_code: str, actor: str, source: str
) -> dict:
    """回执在列：同一事务内把任务行与正式册一起晋升为正式完成。"""
    now = now_iso()
    state = receipt_state(conn, bl_code)
    if not state["receipt_ok"]:
        return state
    conn.execute(
        """UPDATE customs_stub_jobs
           SET status=?, completed_at=COALESCE(completed_at,?), shadow_completed_at=NULL
           WHERE bl_code=? AND TRIM(COALESCE(receipt_ref,''))<>''""",
        (OFFICIAL_DONE, now, bl_code),
    )
    conn.execute(
        """UPDATE bl_manifests
           SET customs_status=?, customs_completed_at=COALESCE(customs_completed_at,?),
               customs_shadow_at=NULL, customs_receipt_ref=?
           WHERE bl_code=?""",
        (OFFICIAL_DONE, now, state["receipt_ref"], bl_code),
    )
    write_audit(
        conn,
        actor,
        "customs_official_complete",
        bl_code,
        {"source": source, "receipt_ref": state["receipt_ref"], "job_id": state["job_id"]},
    )
    return receipt_state(conn, bl_code)


def record_receipt(
    conn: sqlite3.Connection, job_id: int, receipt_ref: str, actor: str
) -> dict:
    """回执到达：原子晋升。回执列落库与正式完成同一事务。"""
    job = conn.execute(
        "SELECT * FROM customs_stub_jobs WHERE id=?", (job_id,)
    ).fetchone()
    if job is None:
        raise LookupError("空桩任务不存在")
    ref = (receipt_ref or "").strip()
    if not ref:
        raise ValueError("回执号不得为空")
    now = now_iso()
    conn.execute(
        """UPDATE customs_stub_jobs
           SET receipt_ref=?, receipt_at=?, status=?, completed_at=?, shadow_completed_at=NULL
           WHERE id=?""",
        (ref, now, OFFICIAL_DONE, now, job_id),
    )
    conn.execute(
        """UPDATE bl_manifests
           SET customs_status=?, customs_completed_at=?, customs_shadow_at=NULL,
               customs_receipt_ref=?
           WHERE bl_code=?""",
        (OFFICIAL_DONE, now, ref, job["bl_code"]),
    )
    write_audit(
        conn,
        actor,
        "customs_receipt",
        job["bl_code"],
        {"job_id": job_id, "receipt_ref": ref},
    )
    return receipt_state(conn, job["bl_code"])
