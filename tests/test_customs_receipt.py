"""第26号修复：海关空桩回执口径（回执禁完 / 影子待回执 / 同票抢锁）。

三红用例先行：
- 甲: 无回执时任何完成口不得把完成态写进正式册；回执到达后原子晋升。
- 乙: 对账不得把影子完成当结案。
- 争用: 两人同票并发结案，后到只留争用痕、不写业务。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import app
from db import connect, init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user: str = "admin", password: str = "admin123") -> dict:
    token = c.post(
        "/api/login", json={"username": user, "password": password}
    ).json()["token"]
    return {"Authorization": "Bearer " + token}


def _register_bl(c: TestClient, h: dict, bl_code: str, declared: float = 20.0) -> None:
    r = c.post(
        "/api/bl",
        headers=h,
        json={
            "bl_code": bl_code,
            "cargo_desc": "回执修复夹具",
            "declared_gross_t": declared,
            "vessel_voyage": "回执轮/026",
        },
    )
    assert r.status_code == 200, r.text


def _weigh_ticket(
    c: TestClient, h: dict, bl_code: str, ticket_code: str, gross: float = 20.0
) -> dict:
    lanes = c.get("/api/lanes", headers=h).json()["items"]
    lane = next(x for x in lanes if x["lane_code"] == "LN-N-01-IN")
    half = round(gross / 2, 3)
    r = c.post(
        "/api/tickets",
        headers=h,
        json={
            "lane_id": lane["id"],
            "bl_ref": bl_code,
            "vehicle_plate": "回执测·026",
            "axle_weights": [{"axle": 1, "w": half}, {"axle": 2, "w": half}],
            "ticket_code": ticket_code,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "已裁定", body
    return body


def _enqueue_customs(c: TestClient, h: dict, bl_code: str) -> dict:
    r = c.post(
        "/api/customs-stub",
        headers=h,
        json={"bl_code": bl_code, "row_count": 2, "mode": "dry_run"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _job_row(job_id: int):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM customs_stub_jobs WHERE id=?", (job_id,)
        ).fetchone()


def _bl_row(bl_code: str):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM bl_manifests WHERE bl_code=?", (bl_code,)
        ).fetchone()


# ---------------------------------------------------------------------------
# 甲：无回执仍正式完成 —— 必须复现为红
# ---------------------------------------------------------------------------
def test_a_no_receipt_never_writes_official_completion():
    c = TestClient(app)
    h = _auth(c)
    bl_code = "BL-RCPT-26-A"
    _register_bl(c, h, bl_code)
    ticket = _weigh_ticket(c, h, bl_code, "WT-RCPT-26-A")
    job = _enqueue_customs(c, h, bl_code)

    released = c.post(
        "/api/gate/release",
        headers=h,
        json={"ticket_id": ticket["id"], "officer": "回执甲"},
    )
    assert released.status_code == 200, released.text
    body = released.json()

    # 完成口必须读空桩回执列；回执为空时禁止正式完成。
    assert body["customs_complete"] is False
    assert body["customs_status"] == "影子待回执"
    assert body["customs_receipt_ok"] is False

    bl = _bl_row(bl_code)
    job_row = _job_row(job["id"])
    assert bl["customs_status"] != "已完成"
    assert bl["customs_completed_at"] is None
    assert job_row["status"] != "已完成"
    assert not (job_row["receipt_ref"] or "").strip()
    assert job_row["shadow_completed_at"] is not None

    audit = c.get("/api/audit?limit=100", headers=h).json()["items"]
    assert not any(
        a["action"] == "gate_release" and (a.get("payload") or {}).get("customs_done") is True
        for a in audit
    )
    assert any(a["action"] == "customs_receipt_deviation" for a in audit)

    # 回执到达 -> 同一事务原子晋升为正式完成。
    receipt = c.post(
        "/api/customs/receipt",
        headers=h,
        json={"job_id": job["id"], "receipt_ref": "CUS-RCPT-26-A-001"},
    )
    assert receipt.status_code == 200, receipt.text
    assert receipt.json()["official"] is True
    assert receipt.json()["status"] == "已完成"

    bl = _bl_row(bl_code)
    job_row = _job_row(job["id"])
    assert bl["customs_status"] == "已完成"
    assert bl["customs_receipt_ref"] == "CUS-RCPT-26-A-001"
    assert job_row["status"] == "已完成"


# ---------------------------------------------------------------------------
# 乙：对账影子完成当结案 —— 必须复现为红
# ---------------------------------------------------------------------------
def test_b_reconcile_must_not_settle_shadow_completion():
    c = TestClient(app)
    h = _auth(c)
    bl_code = "BL-RCPT-26-B"
    _register_bl(c, h, bl_code)
    ticket = _weigh_ticket(c, h, bl_code, "WT-RCPT-26-B")
    job = _enqueue_customs(c, h, bl_code)

    released = c.post(
        "/api/gate/release",
        headers=h,
        json={"ticket_id": ticket["id"], "officer": "回执乙"},
    )
    assert released.status_code == 200, released.text
    sealed = c.post("/api/seals", headers=h, json={"ticket_id": ticket["id"]})
    assert sealed.status_code == 200, sealed.text

    recon = c.get(f"/api/reconcile/{bl_code}", headers=h).json()
    # 账实虽平、也有闸代快照，但回执未到，影子完成不得结案。
    assert recon["proof_ready"] is False
    assert recon["settled"] is False
    assert recon["customs"]["official"] is False
    assert recon["customs"]["shadow"] is True
    assert recon["customs"]["status"] == "影子待回执"
    assert recon["customs"]["receipt_missing"] is True
    assert "回执" in recon["reason"]

    proof = c.get("/api/proof/ticket/WT-RCPT-26-B", headers=h).json()
    assert proof["coherent"] is False
    assert proof["customs_official"] is False
    assert proof["customs_status"] == "影子待回执"

    receipt = c.post(
        "/api/customs/receipt",
        headers=h,
        json={"job_id": job["id"], "receipt_ref": "CUS-RCPT-26-B-001"},
    )
    assert receipt.status_code == 200, receipt.text

    recon = c.get(f"/api/reconcile/{bl_code}", headers=h).json()
    assert recon["customs"]["official"] is True
    assert recon["proof_ready"] is True
    assert recon["settled"] is True
    proof = c.get("/api/proof/ticket/WT-RCPT-26-B", headers=h).json()
    assert proof["coherent"] is True


# ---------------------------------------------------------------------------
# 争用：两人同票并发抢写结案 —— 后到只留争用痕
# ---------------------------------------------------------------------------
def test_c_same_ticket_release_contention_leaves_trace_only():
    c = TestClient(app)
    h_admin = _auth(c, "admin", "admin123")
    h_writer = _auth(c, "writer", "writer123")
    bl_code = "BL-RCPT-26-C"
    _register_bl(c, h_admin, bl_code)
    ticket = _weigh_ticket(c, h_admin, bl_code, "WT-RCPT-26-C")
    _enqueue_customs(c, h_admin, bl_code)
    ticket_id = ticket["id"]

    barrier = threading.Barrier(2)

    def fire(headers: dict, officer: str) -> int:
        client = TestClient(app)
        barrier.wait(timeout=10)
        r = client.post(
            "/api/gate/release",
            headers=headers,
            json={"ticket_id": ticket_id, "officer": officer},
        )
        return r.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(fire, h_admin, "并发甲"),
            pool.submit(fire, h_writer, "并发乙"),
        ]
        statuses = sorted(f.result(timeout=30) for f in futures)

    assert statuses == [200, 409], statuses

    with connect() as conn:
        releases = conn.execute(
            "SELECT * FROM gate_releases WHERE ticket_id=?", (ticket_id,)
        ).fetchall()
        traces = conn.execute(
            "SELECT * FROM contention_log WHERE lock_key=?",
            (f"ticket:{ticket_id}",),
        ).fetchall()
    assert len(releases) == 1
    assert traces, "后到未留争用痕"


# ---------------------------------------------------------------------------
# 五页同源：空桩/放行/对账/提单/总览 读同一回执列
# ---------------------------------------------------------------------------
def test_d_five_pages_share_one_receipt_source():
    c = TestClient(app)
    h = _auth(c)
    bl_code = "BL-RCPT-26-D"
    _register_bl(c, h, bl_code)
    ticket = _weigh_ticket(c, h, bl_code, "WT-RCPT-26-D")
    job = _enqueue_customs(c, h, bl_code)

    # 入队响应不得出现「已完成」。
    stub = c.post(
        "/api/customs-stub",
        headers=h,
        json={"bl_code": bl_code, "row_count": 1, "mode": "dry_run"},
    ).json()
    assert stub["status"] == "已入队"
    assert stub["receipt_ok"] is False
    assert stub["official"] is False

    released = c.post(
        "/api/gate/release",
        headers=h,
        json={"ticket_id": ticket["id"], "officer": "五页"},
    ).json()
    assert released["customs_status"] == "影子待回执"

    job_state = c.get(f"/api/customs/state/{bl_code}", headers=h).json()
    recon = c.get(f"/api/reconcile/{bl_code}", headers=h).json()
    bl_detail = c.get(f"/api/bl/{bl_code}", headers=h).json()
    bl_list = next(
        b for b in c.get("/api/bl", headers=h).json()["items"] if b["bl_code"] == bl_code
    )
    releases = c.get("/api/gate/releases", headers=h).json()["items"]
    release_row = next(r for r in releases if r["ticket_id"] == ticket["id"])
    dash = c.get("/api/dashboard", headers=h).json()

    statuses = {
        job_state["status"],
        recon["customs"]["status"],
        bl_detail["customs"]["status"],
        bl_list["customs_status"],
        release_row["customs_status"],
    }
    assert statuses == {"影子待回执"}, statuses
    assert dash["customs_official"] == 0
    assert dash["customs_shadow"] >= 1

    # 回执拒绝空号，也不因空号晋升。
    bad = c.post(
        "/api/customs/receipt",
        headers=h,
        json={"job_id": job["id"], "receipt_ref": "   "},
    )
    assert bad.status_code == 422
    assert c.get(f"/api/customs/state/{bl_code}", headers=h).json()["official"] is False

    ok = c.post(
        "/api/customs/receipt",
        headers=h,
        json={"job_id": job["id"], "receipt_ref": "CUS-RCPT-26-D-001"},
    )
    assert ok.status_code == 200
    assert ok.json()["official"] is True

    dash = c.get("/api/dashboard", headers=h).json()
    assert dash["customs_official"] >= 1
    # 结案还须闸代快照；补齐后回执与快照同一口径。
    sealed = c.post("/api/seals", headers=h, json={"ticket_id": ticket["id"]})
    assert sealed.status_code == 200, sealed.text
    assert c.get(f"/api/reconcile/{bl_code}", headers=h).json()["settled"] is True


def test_e_receipt_job_must_exist():
    c = TestClient(app)
    h = _auth(c)
    missing = c.post(
        "/api/customs/receipt",
        headers=h,
        json={"job_id": 999999, "receipt_ref": "CUS-X"},
    )
    assert missing.status_code == 404

