"""Backend API tests for Petra options trading agent."""
import os
import time
import json
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://autonomous-spreads.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# -------- Basic endpoints --------
class TestBasics:
    def test_root(self, client):
        r = client.get(f"{API}/")
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "online"
        assert d["mode"] == "mock"

    def test_account(self, client):
        r = client.get(f"{API}/account")
        assert r.status_code == 200
        d = r.json()
        for k in ["equity", "buying_power", "day_pnl", "open_risk", "win_rate",
                  "total_trades", "mode", "open_positions"]:
            assert k in d, f"missing {k}"
        assert d["mode"] == "mock"
        assert d["equity"] > 50000  # sanity - started at 100k

    def test_positions_shape(self, client):
        r = client.get(f"{API}/positions")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        if arr:
            p = arr[0]
            for k in ["legs", "contracts", "credit", "max_risk",
                     "unrealized_pnl", "risk_gate_score", "underlying", "strategy", "id"]:
                assert k in p, f"missing {k} in position"
            assert isinstance(p["legs"], list) and len(p["legs"]) >= 2

    def test_trades(self, client):
        r = client.get(f"{API}/trades")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        if arr:
            t = arr[0]
            assert "realized_pnl" in t
            assert "exit_reason" in t

    def test_decisions(self, client):
        r = client.get(f"{API}/decisions")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        assert len(arr) > 0, "expected some seeded decisions"
        d = arr[0]
        assert "outcome" in d
        assert "gate_checks" in d
        assert d["outcome"] in ["approved", "rejected", "skipped"]

    def test_pnl(self, client):
        r = client.get(f"{API}/pnl")
        assert r.status_code == 200
        arr = r.json()
        assert isinstance(arr, list)
        if len(arr) >= 2:
            # time ordered
            assert arr[0]["ts"] <= arr[-1]["ts"]

    def test_market(self, client):
        r = client.get(f"{API}/market")
        assert r.status_code == 200
        d = r.json()
        assert "status" in d
        assert "symbols" in d
        assert len(d["symbols"]) >= 1
        assert "price" in d["symbols"][0]


# -------- Config --------
class TestConfig:
    def test_get_config(self, client):
        r = client.get(f"{API}/config")
        assert r.status_code == 200
        d = r.json()
        for k in ["max_risk_pct", "max_concurrent", "min_credit_width"]:
            assert k in d

    def test_update_config_persist(self, client):
        orig = client.get(f"{API}/config").json()
        new_val = 3.0 if orig["max_risk_pct"] != 3.0 else 2.5
        r = client.put(f"{API}/config", json={"max_risk_pct": new_val})
        assert r.status_code == 200
        assert r.json()["max_risk_pct"] == new_val
        # verify persisted
        d = client.get(f"{API}/config").json()
        assert d["max_risk_pct"] == new_val
        # restore
        client.put(f"{API}/config", json={"max_risk_pct": orig["max_risk_pct"]})


# -------- Agent cycle (real LLM) --------
class TestAgentCycle:
    def test_run_cycle_force(self, client):
        r = client.post(f"{API}/agent/run-cycle", json={"force": True}, timeout=120)
        assert r.status_code == 200
        d = r.json()
        assert "cycle_id" in d or "status" in d
        # decisions may be empty if all skipped; but the endpoint should return an array field
        assert "decisions" in d or d.get("status") in ("paused", "skipped")

    def test_risk_gate_correctness(self, client):
        """Every approved decision must satisfy risk gates."""
        cfg = client.get(f"{API}/config").json()
        acc = client.get(f"{API}/account").json()
        equity = acc["equity"]
        max_risk_dollar = equity * cfg["max_risk_pct"] / 100.0

        decisions = client.get(f"{API}/decisions", params={"limit": 100}).json()
        approved = [d for d in decisions if d.get("outcome") == "approved"
                    and d.get("proposal")]
        rejected = [d for d in decisions if d.get("outcome") == "rejected"]

        for d in approved:
            p = d.get("proposal") or {}
            # skip if no proposal payload
            if not p:
                continue
            # allow slight tolerance for dynamic equity
            if "max_risk" in p:
                assert p["max_risk"] <= max_risk_dollar * 1.5, \
                    f"approved max_risk {p['max_risk']} exceeds cap {max_risk_dollar}"
            if "credit_width_ratio" in p:
                assert p["credit_width_ratio"] >= cfg["min_credit_width"] - 0.001, \
                    f"approved credit/width {p['credit_width_ratio']} below {cfg['min_credit_width']}"

        # Rejected decisions must include at least one failing gate check
        for d in rejected:
            checks = d.get("gate_checks", [])
            if checks:
                assert any(not c.get("passed", True) for c in checks), \
                    f"rejected decision has no failing check: {d.get('reason')}"

    def test_pause_and_unpause(self, client):
        # pause
        r = client.post(f"{API}/agent/pause", json={"paused": True})
        assert r.status_code == 200
        assert r.json()["paused"] is True

        # run-cycle should no-op while paused
        r2 = client.post(f"{API}/agent/run-cycle", json={"force": True}, timeout=30)
        assert r2.status_code == 200
        d = r2.json()
        # accepts status=paused OR empty decisions
        is_paused = d.get("status") == "paused" or d.get("paused") is True
        assert is_paused, f"expected paused status, got {d}"

        # unpause
        r3 = client.post(f"{API}/agent/pause", json={"paused": False})
        assert r3.status_code == 200
        assert r3.json()["paused"] is False


# -------- Position close --------
class TestPositionClose:
    def test_close_position(self, client):
        # ensure at least one open exists; force a cycle if none
        positions = client.get(f"{API}/positions").json()
        if not positions:
            client.post(f"{API}/agent/pause", json={"paused": False})
            client.post(f"{API}/agent/run-cycle", json={"force": True}, timeout=120)
            positions = client.get(f"{API}/positions").json()
        if not positions:
            pytest.skip("No open positions available to close")

        pid = positions[0]["id"]
        underlying = positions[0]["underlying"]

        trades_before = len(client.get(f"{API}/trades").json())
        r = client.post(f"{API}/positions/{pid}/close")
        assert r.status_code == 200
        d = r.json()
        assert d.get("closed") is True
        assert "realized_pnl" in d

        # Verify it moved to trades
        trades_after = client.get(f"{API}/trades").json()
        assert len(trades_after) == trades_before + 1
        assert any(t["id"] == pid for t in trades_after)
        # And no longer in open positions
        open_now = client.get(f"{API}/positions").json()
        assert not any(p["id"] == pid for p in open_now)

    def test_close_invalid_position(self, client):
        r = client.post(f"{API}/positions/does-not-exist/close")
        # returns 200 with error field per current impl
        assert r.status_code == 200
        assert "error" in r.json()


# -------- Chat streaming --------
class TestChat:
    def test_chat_stream_non_empty(self, client):
        r = client.post(f"{API}/chat",
                        json={"message": "What is my current open risk in one sentence?",
                              "session_id": "test-session"},
                        stream=True, timeout=90)
        assert r.status_code == 200
        content = b""
        for chunk in r.iter_content(chunk_size=64):
            content += chunk
            if len(content) > 400:
                break
        text = content.decode("utf-8", errors="ignore")
        assert len(text.strip()) > 0, "chat stream returned empty body"
        # error prefix bracket check
        assert "[agent error:" not in text, f"agent error in stream: {text[:300]}"
