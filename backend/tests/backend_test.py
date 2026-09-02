"""Backend API tests for Petra options trading agent — LIVE Alpaca paper mode."""
import os
import time
import pytest
import requests

def _load_frontend_env():
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _load_frontend_env() or "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL not set"
API = f"{BASE_URL}/api"

EXPECTED_UNIVERSE = {"SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "META"}
EXPECTED_ACCOUNT_ID = "PA39X74UN8VF"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# -------- Basic endpoints --------
class TestBasics:
    def test_root(self, client):
        r = client.get(f"{API}/", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "online"
        assert d["mode"] == "live"

    def test_account_live(self, client):
        r = client.get(f"{API}/account", timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ["equity", "buying_power", "day_pnl", "total_pnl", "open_risk",
                  "win_rate", "total_trades", "mode", "open_positions", "account_id"]:
            assert k in d, f"missing {k}"
        assert d["mode"] == "live"
        assert d["account_id"] == EXPECTED_ACCOUNT_ID
        assert isinstance(d["equity"], (int, float)) and d["equity"] > 50000
        assert isinstance(d["day_pnl"], (int, float))
        assert isinstance(d["total_pnl"], (int, float))
        # No ObjectId leak
        assert "_id" not in d

    def test_status_live(self, client):
        r = client.get(f"{API}/status", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["mode"] == "live"
        assert d["cycle_seconds"] == 900
        assert "agent" in d and "market" in d
        assert "open" in d["market"]

    def test_market_live(self, client):
        r = client.get(f"{API}/market", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "symbols" in d
        syms = {s["symbol"]: s for s in d["symbols"]}
        assert set(syms.keys()) == EXPECTED_UNIVERSE
        for sym, row in syms.items():
            for k in ("price", "change_pct", "iv", "trend"):
                assert isinstance(row[k], (int, float)), f"{sym}.{k} not numeric"
            assert row["price"] > 0
        # SPY must be a real price ~760 range, definitely not the mock 545
        assert syms["SPY"]["price"] > 600, f"SPY price {syms['SPY']['price']} looks like mock"

    def test_pnl(self, client):
        r = client.get(f"{API}/pnl", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, list) and len(d) >= 1
        assert "equity" in d[0] and "ts" in d[0]

    def test_positions(self, client):
        r = client.get(f"{API}/positions", timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_trades(self, client):
        r = client.get(f"{API}/trades", timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_decisions(self, client):
        r = client.get(f"{API}/decisions", timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# -------- Config CRUD --------
class TestConfig:
    def test_get_config(self, client):
        r = client.get(f"{API}/config", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "min_open_interest" in d
        assert "max_concurrent" in d

    def test_update_config_roundtrip(self, client):
        orig = client.get(f"{API}/config", timeout=30).json()
        original_oi = orig["min_open_interest"]
        # set to 300
        r = client.put(f"{API}/config", json={"min_open_interest": 300}, timeout=30)
        assert r.status_code == 200
        assert r.json()["min_open_interest"] == 300
        # verify persisted
        assert client.get(f"{API}/config", timeout=30).json()["min_open_interest"] == 300
        # restore
        r2 = client.put(f"{API}/config", json={"min_open_interest": original_oi}, timeout=30)
        assert r2.status_code == 200
        assert r2.json()["min_open_interest"] == original_oi


# -------- Position close 404 --------
class TestPositionClose:
    def test_bad_id_returns_404(self, client):
        r = client.post(f"{API}/positions/nonexistent-abc-123/close", timeout=30)
        assert r.status_code == 404


# -------- Agent cycle --------
class TestAgentCycle:
    def test_cycle_market_closed(self, client):
        """Without force, while market is closed, expect status market_closed."""
        # First verify market is closed via /status
        st = client.get(f"{API}/status", timeout=30).json()
        if st["market"]["open"]:
            pytest.skip("Market is currently OPEN — market_closed path cannot be tested")
        r = client.post(f"{API}/agent/run-cycle", json={}, timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "market_closed"
        assert d["decisions"] == []

    def test_cycle_forced(self, client):
        """Force cycle — takes ~20-60s. Verify shape, no 500s, no position persisted for unfilled orders."""
        pre_positions = client.get(f"{API}/positions", timeout=30).json()
        pre_pos_ids = {p["id"] for p in pre_positions}

        r = client.post(f"{API}/agent/run-cycle", json={"force": True}, timeout=180)
        assert r.status_code == 200, f"cycle failed: {r.status_code} {r.text[:400]}"
        d = r.json()
        assert d["status"] == "ran"
        assert "decisions" in d and isinstance(d["decisions"], list)

        # Validate each decision
        market_is_open = client.get(f"{API}/status", timeout=30).json()["market"]["open"]
        for dec in d["decisions"]:
            assert dec["outcome"] in {"approved", "rejected", "error", "skipped"}, dec["outcome"]
            # market snapshot / expiry checks on decisions that ran through the pipeline
            if dec.get("market_snapshot"):
                snap = dec["market_snapshot"]
                # Not all decisions carry expiry (only when a chain was loaded)
                if "expiry" in snap:
                    assert isinstance(snap["expiry"], str)
            # rejected via risk_gate should carry gate_checks
            if dec["outcome"] == "rejected" and dec.get("gate_checks"):
                assert isinstance(dec["gate_checks"], list) and len(dec["gate_checks"]) > 0
            # unfilled while market closed → "error" with 'not filled' language
            if dec["outcome"] == "error" and not market_is_open and "not filled" in dec.get("reason", "").lower():
                # verify no persisted position for this decision
                assert dec.get("position_id") in (None, "")

        # verify no orphan positions were persisted for unfilled/error decisions
        post_positions = client.get(f"{API}/positions", timeout=30).json()
        new_pos_ids = {p["id"] for p in post_positions} - pre_pos_ids
        # approved decision ids
        approved_pids = {dec.get("position_id") for dec in d["decisions"] if dec["outcome"] == "approved"}
        approved_pids.discard(None)
        approved_pids.discard("")
        # every new position must correspond to an approved decision
        assert new_pos_ids.issubset(approved_pids), f"orphan positions: {new_pos_ids - approved_pids}"


# -------- Chat streaming --------
class TestChat:
    def test_chat_streams(self, client):
        r = client.post(f"{API}/chat", json={"message": "How is the account looking?", "session_id": "test-live"},
                        timeout=120, stream=True)
        assert r.status_code == 200
        collected = b""
        for chunk in r.iter_content(chunk_size=64):
            if chunk:
                collected += chunk
            if len(collected) > 40:
                break
        r.close()
        text = collected.decode("utf-8", errors="ignore")
        assert len(text.strip()) > 0
        assert "[agent error" not in text
