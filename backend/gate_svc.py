"""闸口放行：须无开放超重；完成态须读海关空桩回执列。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user, require_writer
from customs_receipt import (
    DEVIATION_ACTION,
    mark_official_completion,
    mark_shadow_completion,
    receipt_state,
)
from db import connect, now_iso, write_audit
from locks import log_contention, release, require_lock
from models import GateReleaseIn
from validators import validate_no_open_overload, validate_ticket_mutable

router = APIRouter(tags=["gate"])


@router.get("/api/gate/releases")
def list_releases(authorization: AuthHeader = None):
    require_user(authorization)
    with connect() as conn:
        rows = conn.execute(
            """SELECT g.*, t.ticket_code, t.vehicle_plate, l.lane_code
               FROM gate_releases g
               JOIN weigh_tickets t ON t.id=g.ticket_id
               JOIN weigh_lanes l ON l.id=g.lane_id
               ORDER BY g.id DESC"""
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            ticket = conn.execute(
                "SELECT bl_ref FROM weigh_tickets WHERE id=?", (d["ticket_id"],)
            ).fetchone()
            state = receipt_state(conn, ticket["bl_ref"]) if ticket else None
            d["customs_status"] = state["status"] if state else "未申报"
            d["customs_receipt_ok"] = state["receipt_ok"] if state else False
            items.append(d)
        return {"items": items}


@router.post("/api/gate/release")
def release_gate(body: GateReleaseIn, authorization: AuthHeader = None):
    user = require_writer(authorization)
    lock_key = f"ticket:{body.ticket_id}"
    with connect() as conn:
        require_lock(conn, lock_key, user)
        try:
            ticket = conn.execute(
                """SELECT t.*, l.id lane_id FROM weigh_tickets t
                   JOIN weigh_lanes l ON l.id=t.lane_id WHERE t.id=?""",
                (body.ticket_id,),
            ).fetchone()
            if not ticket:
                raise HTTPException(404, "过磅票不存在")
            if ticket["status"] == "已放行":
                # 后到者不写业务，只留争用痕。
                log_contention(lock_key, user, "后到：重复放行完成")
                raise HTTPException(409, "已放行")
            validate_ticket_mutable(dict(ticket))
            if ticket["status"] not in ("已裁定", "旁路挂起"):
                raise HTTPException(409, "票态不允许放行")
            if ticket["status"] == "旁路挂起":
                raise HTTPException(409, "旁路挂起须先闭环")
            validate_no_open_overload(conn, body.ticket_id)
            exists = conn.execute(
                "SELECT id FROM gate_releases WHERE ticket_id=? AND status='已放行'",
                (body.ticket_id,),
            ).fetchone()
            if exists:
                log_contention(lock_key, user, "后到：重复放行完成")
                raise HTTPException(409, "已放行")
            now = now_iso()
            # 完成写口以票态做原子比较交换：并发下只有一路能改到“已放行”。
            claimed = conn.execute(
                "UPDATE weigh_tickets SET status='已放行' WHERE id=? AND status='已裁定'",
                (body.ticket_id,),
            )
            if claimed.rowcount != 1:
                log_contention(lock_key, user, "后到：完成写口已被抢")
                raise HTTPException(409, "争用：完成写口已被抢，后到不写业务")
            ref = f"GR-{ticket['ticket_code']}-{now[:10]}"
            cur = conn.execute(
                """INSERT INTO gate_releases(ticket_id,lane_id,release_ref,status,officer,released_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    body.ticket_id,
                    ticket["lane_id"],
                    ref,
                    "已放行",
                    body.officer,
                    now,
                ),
            )
            # 完成口必须读空桩回执列：有回执才正式完成，否则只影子待回执。
            state = receipt_state(conn, ticket["bl_ref"])
            if state["receipt_ok"]:
                state = mark_official_completion(
                    conn, ticket["bl_ref"], user, source="gate_release"
                )
            else:
                state = mark_shadow_completion(
                    conn, ticket["bl_ref"], user, source="gate_release"
                )
            write_audit(
                conn,
                user,
                "gate_release",
                ref,
                {
                    "release_id": cur.lastrowid,
                    "ticket_id": body.ticket_id,
                    "customs_done": state["official"],
                    "customs_status": state["status"],
                    "customs_receipt_ok": state["receipt_ok"],
                    "deviation": None if state["official"] else DEVIATION_ACTION,
                },
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "release_ref": ref,
                "status": "已放行",
                "customs_complete": state["official"],
                "customs_status": state["status"],
                "customs_receipt_ok": state["receipt_ok"],
            }
        finally:
            release(conn, lock_key, user)
