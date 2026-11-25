"""海关申报空桩（可挂载接口）。

入队只到「已入队」；回执由 /api/customs/receipt 单独落库后原子晋升。
无回执不得在响应或册上出现「已完成」——回执禁完。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from customs_receipt import (
    JOB_QUEUED,
    SHADOW_PENDING,
    receipt_state,
    record_receipt,
)
from db import connect, now_iso, write_audit
from models import StubJobIn

router = APIRouter(tags=["customs_stub"])


class CustomsStubIn(StubJobIn):
    bl_code: str
    row_count: int = 0


class CustomsReceiptIn(StubJobIn):
    job_id: int
    receipt_ref: str


@router.get("/api/customs/jobs")
def list_jobs(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM customs_stub_jobs ORDER BY id DESC LIMIT 100"
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d["receipt_ok"] = bool((d.get("receipt_ref") or "").strip())
            items.append(d)
        return {"items": items, "stub": True, "status": "空桩未对接"}


@router.get("/api/customs/state/{bl_code}")
def customs_state(bl_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        return receipt_state(conn, bl_code)


@router.post("/api/customs-stub")
def enqueue_stub(body: CustomsStubIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        bl = conn.execute(
            "SELECT bl_code FROM bl_manifests WHERE bl_code=?", (body.bl_code,)
        ).fetchone()
        if not bl:
            raise HTTPException(422, "提单须先登记")
        now = now_iso()
        # 同一提单未回执的任务并入已有未完成任务行，避免多行各说各话。
        open_job = conn.execute(
            """SELECT id, shadow_completed_at FROM customs_stub_jobs
               WHERE bl_code=? AND TRIM(COALESCE(receipt_ref,''))=''
               ORDER BY id DESC LIMIT 1""",
            (body.bl_code,),
        ).fetchone()
        if open_job:
            job_id = int(open_job["id"])
            # 已记影子的任务不因再入队退回入队态。
            job_status = (
                SHADOW_PENDING if open_job["shadow_completed_at"] else JOB_QUEUED
            )
            conn.execute(
                """UPDATE customs_stub_jobs SET row_count=?, mode=?, status=?, detail=?
                   WHERE id=?""",
                (
                    body.row_count,
                    body.mode,
                    job_status,
                    body.detail or "海关空桩预演",
                    job_id,
                ),
            )
        else:
            cur = conn.execute(
                """INSERT INTO customs_stub_jobs(bl_code,row_count,mode,status,detail,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    body.bl_code,
                    body.row_count,
                    body.mode,
                    JOB_QUEUED,
                    body.detail or "海关空桩预演",
                    now,
                ),
            )
            job_id = int(cur.lastrowid)
        # 只把未申报的提单推进到入队态，不回退影子/正式完成。
        conn.execute(
            """UPDATE bl_manifests SET customs_status=?
               WHERE bl_code=? AND customs_status='未申报'""",
            ("已入队", body.bl_code),
        )
        write_audit(conn, user, "customs_stub", body.bl_code, {"job_id": job_id})
        conn.commit()
        state = receipt_state(conn, body.bl_code)
        return {
            "id": job_id,
            "status": state["status"],
            "message": "空桩已入队；回执未到，不写完成",
            "receipt_ok": state["receipt_ok"],
            "official": state["official"],
        }


@router.post("/api/customs/receipt")
def post_receipt(body: CustomsReceiptIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    with connect() as conn:
        try:
            state = record_receipt(conn, body.job_id, body.receipt_ref, user)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        conn.commit()
        return {
            "job_id": body.job_id,
            "bl_code": state["bl_code"],
            "status": state["status"],
            "official": state["official"],
            "receipt_ref": state["receipt_ref"],
        }
