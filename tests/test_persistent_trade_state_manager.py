"""Close-evidence persistence contract for PersistentTradeStateManager.

A successful broker close returns close_price and a broker timestamp,
but close_trade records neither, so every closed trade is scored as if
it exited at 0.0. These assertions pin the backward-compatible API that
lets a caller persist that evidence, keep the local transition clock
separate from the broker clock, and prove the persisted close_price
feeds the repository's real performance calculation.
"""

from datetime import datetime

import pytest

from execution.performance_metrics import compute_performance_metrics
from execution.persistent_trade_state_manager import (
    PersistentTradeStateManager,
)


_BROKER_EXIT_TIMESTAMP = "2026-07-26T23:55:12.000000000Z"


def _trade(request_id, *, status):
    return {
        "request_id": request_id,
        "currency_pair": "USD/CAD",
        "direction": "Long",
        "position_size": 94517,
        "fill_price": 1.40898,
        "status": status,
        "created_at": "2026-07-26T23:50:00+00:00",
    }


def _record(manager, request_id):
    for trade in manager.get_all_trades():
        if trade.get("request_id") == request_id:
            return trade
    return None


def test_close_trade_persists_supplied_broker_close_evidence(tmp_path):
    db_path = tmp_path / "trade_state.db"
    manager = PersistentTradeStateManager(db_path=str(db_path))

    try:
        manager.record_trade(_trade("TEST-CLOSE-EVIDENCE-1", status="FILLED"))
        manager.record_trade(_trade("TEST-CLOSE-EVIDENCE-2", status="FILLED"))
        manager.record_trade(_trade("TEST-CLOSE-EVIDENCE-3", status="PENDING"))

        # --- Evidence-bearing close ---
        manager.close_trade(
            "TEST-CLOSE-EVIDENCE-1",
            close_price=1.40861,
            exit_timestamp=_BROKER_EXIT_TIMESTAMP,
        )

        evidence = _record(manager, "TEST-CLOSE-EVIDENCE-1")
        assert evidence["status"] == "CLOSED"
        assert evidence["close_price"] == 1.40861
        assert evidence["exit_timestamp"] == _BROKER_EXIT_TIMESTAMP

        closed_at = evidence.get("closed_at")
        assert isinstance(closed_at, str) and closed_at.strip(), (
            "close_trade must still stamp the local state-transition time"
        )
        parsed_closed_at = datetime.fromisoformat(closed_at)
        assert parsed_closed_at.tzinfo is not None
        assert parsed_closed_at.utcoffset() is not None
        assert closed_at != _BROKER_EXIT_TIMESTAMP, (
            "closed_at is the local transition clock and must not be"
            " replaced by the broker exit timestamp"
        )

        # --- Backward compatibility: request_id only ---
        manager.close_trade("TEST-CLOSE-EVIDENCE-2")

        plain = _record(manager, "TEST-CLOSE-EVIDENCE-2")
        assert plain["status"] == "CLOSED"
        assert isinstance(plain.get("closed_at"), str)
        assert plain["closed_at"].strip()
        assert "close_price" not in plain, (
            "a close without evidence must not add a close_price key"
        )
        assert "exit_timestamp" not in plain, (
            "a close without evidence must not add an exit_timestamp key"
        )

        # --- Already-closed rows are never restamped or overwritten ---
        before_repeat = dict(evidence)
        manager.close_trade(
            "TEST-CLOSE-EVIDENCE-1",
            close_price=9.99999,
            exit_timestamp="2099-01-01T00:00:00.000000000Z",
        )
        assert _record(manager, "TEST-CLOSE-EVIDENCE-1") == before_repeat, (
            "close_trade must transition FILLED rows only, leaving an"
            " already-closed record byte-identical"
        )

        # --- PENDING rows stay untouched ---
        pending_before = dict(_record(manager, "TEST-CLOSE-EVIDENCE-3"))
        manager.close_trade(
            "TEST-CLOSE-EVIDENCE-3",
            close_price=1.40861,
            exit_timestamp=_BROKER_EXIT_TIMESTAMP,
        )
        pending_after = _record(manager, "TEST-CLOSE-EVIDENCE-3")
        assert pending_after == pending_before
        assert pending_after["status"] == "PENDING"

        # --- The persisted close_price feeds the real calculation ---
        persisted_evidence = _record(manager, "TEST-CLOSE-EVIDENCE-1")
        metrics = compute_performance_metrics([persisted_evidence])
        assert metrics["total_trades"] == 1
        assert metrics["total_profit"] == pytest.approx(
            -34.97129,
            abs=1e-5,
        )
    finally:
        manager.close()
