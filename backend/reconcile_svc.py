"""提单册与过磅票对账证明（读空桩回执列，影子不得结案）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from auth import AuthHeader, require_user
from customs_receipt import receipt_state
from validators import validate_bl_match

router = APIRouter(tags=["reconcile"])


@router.get("/api/reconcile")
def reconcile_all(authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        rows = conn.execute(
            """SELECT bl_code, declared_gross_t FROM bl_manifests ORDER BY bl_code"""
        ).fetchall()
        items = []
        for bl in rows:
            items.append(_reconcile_bl(conn, bl["bl_code"]))
        return {"items": items}


@router.get("/api/reconcile/{bl_code}")
def reconcile_one(bl_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        bl = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
        ).fetchone()
        if not bl:
            raise HTTPException(404, "提单不存在")
        return _reconcile_bl(conn, bl_code)


def _reconcile_bl(conn, bl_code: str) -> dict:
    bl = conn.execute(
        "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
    ).fetchone()
    tickets = conn.execute(
        """SELECT ticket_code, gross_t, status, generation FROM weigh_tickets
           WHERE bl_ref=? AND status NOT IN ('作废')""",
        (bl_code,),
    ).fetchall()
    customs = receipt_state(conn, bl_code)
    if not tickets:
        return {
            "bl_code": bl_code,
            "declared_gross_t": bl["declared_gross_t"],
            "ticket_sum_t": 0,
            "balanced": False,
            "reason": "无过磅票",
            "tickets": [],
            "customs": customs,
            "settled": False,
            "proof_ready": False,
        }
    ticket_sum = round(sum(float(t["gross_t"]) for t in tickets), 3)
    declared = float(bl["declared_gross_t"])
    matched = validate_bl_match(declared, ticket_sum, tolerance=0.1)
    seal_rows = conn.execute(
        """SELECT s.seal_ref, s.ticket_generation, t.ticket_code
           FROM gate_seals s JOIN weigh_tickets t ON t.id=s.ticket_id
           WHERE t.bl_ref=?""",
        (bl_code,),
    ).fetchall()
    # 账实平 + 有闸代快照还不够：回执未到只能影子，不得结案。
    proof_ok = bool(matched and len(seal_rows) >= 1 and customs["official"])
    if customs["official"]:
        reason = "账实已平，回执已到，可结案"
    elif customs["shadow"]:
        reason = "回执缺失：影子完成不得结案"
    elif customs["receipt_missing"] and matched:
        reason = "回执缺失：空桩未回执，不得结案"
    elif not matched:
        reason = "账实偏差待查"
    else:
        reason = "待补证明"
    return {
        "bl_code": bl_code,
        "declared_gross_t": declared,
        "ticket_sum_t": ticket_sum,
        "balanced": matched,
        "proof_ready": proof_ok,
        "settled": proof_ok,
        "seal_count": len(seal_rows),
        "customs": customs,
        "reason": reason,
        "tickets": [dict(t) for t in tickets],
        "seals": [dict(s) for s in seal_rows],
    }


@router.get("/api/proof/ticket/{ticket_code}")
def proof_ticket(ticket_code: str, authorization: AuthHeader = None):
    require_user(authorization)
    from db import connect

    with connect() as conn:
        ticket = conn.execute(
            "SELECT * FROM weigh_tickets WHERE ticket_code=?", (ticket_code,)
        ).fetchone()
        if not ticket:
            raise HTTPException(404, "票不存在")
        bl = conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (ticket["bl_ref"],)
        ).fetchone()
        seal = conn.execute(
            """SELECT * FROM gate_seals WHERE ticket_id=? ORDER BY id DESC LIMIT 1""",
            (ticket["id"],),
        ).fetchone()
        release = conn.execute(
            """SELECT * FROM gate_releases WHERE ticket_id=? AND status='已放行'""",
            (ticket["id"],),
        ).fetchone()
        overload_closed = not conn.execute(
            """SELECT id FROM overload_holds WHERE ticket_id=? AND status='开放'""",
            (ticket["id"],),
        ).fetchone()
        customs = receipt_state(conn, ticket["bl_ref"])
        manifest_match = False
        if seal and bl:
            manifest_match = validate_bl_match(
                float(bl["declared_gross_t"]), float(ticket["gross_t"])
            )
        return {
            "ticket_code": ticket_code,
            "bl_ref": ticket["bl_ref"],
            "gross_t": ticket["gross_t"],
            "manifest_matches_bl": manifest_match,
            "has_seal": seal is not None,
            "seal_generation": seal["ticket_generation"] if seal else None,
            "released": release is not None,
            "overload_closed": overload_closed,
            "customs_official": customs["official"],
            "customs_status": customs["status"],
            "customs_receipt_ok": customs["receipt_ok"],
            # 回执未正式完成则整票不连贯：影子完成不能作为结案证明。
            "coherent": bool(
                manifest_match
                and overload_closed
                and (seal or release)
                and customs["official"]
            ),
        }
