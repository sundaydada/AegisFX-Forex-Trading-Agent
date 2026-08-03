"""No-order readiness contract for the autonomous USD/CAD wiring.

check_readiness inspects configuration and object properties only. It
must never request a quote, read or write proposal or trade rows, call
the signal provider, invoke the executor, or contact OANDA — so every
collaborator here raises if an operational method is reached, and every
call is counted as a second, independent proof.

The wiring module does not exist yet; it is imported inside each test so
collection stays clean either way.
"""

import importlib
from datetime import datetime, timedelta

import pytest


PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"
LIVE_BASE_URL = "https://api-fxtrade.oanda.com"

SENTINEL_API_KEY = "SENTINEL_DEMO_API_KEY"
SENTINEL_ACCOUNT_ID = "SENTINEL_DEMO_ACCOUNT_ID"

REQUIRED_CHECK_NAMES = {
    "credentials_present",
    "configured_practice_endpoint",
    "broker_practice_endpoint",
    "executor_practice_endpoint",
    "database_paths_absolute",
    "state_manager_trade_state_path",
    "proposal_queue_approval_path",
    "executor_database_paths",
    "broker_interface",
    "state_manager_interface",
    "proposal_queue_interface",
    "signal_provider_callable",
    "executor_interface",
    "no_live_endpoint",
}


class _NeverCalledBroker:
    def __init__(self, base_url):
        self.base_url = base_url
        self.get_open_positions_calls = 0
        self.get_quote_calls = 0

    def get_open_positions(self):
        self.get_open_positions_calls += 1
        raise AssertionError("readiness must not read broker positions")

    def get_quote(self, pair):
        self.get_quote_calls += 1
        raise AssertionError("readiness must not request a quote")


class _NeverCalledStateManager:
    def __init__(self, db_path):
        self.db_path = db_path
        self.get_all_trades_calls = 0

    def get_all_trades(self):
        self.get_all_trades_calls += 1
        raise AssertionError("readiness must not read the trade ledger")


class _NeverCalledProposalQueue:
    def __init__(self, db_path):
        self.db_path = db_path
        self.add_proposals_calls = 0
        self.approve_proposal_calls = 0
        self.get_approved_proposals_calls = 0

    def add_proposals(self, proposals):
        self.add_proposals_calls += 1
        raise AssertionError("readiness must not write proposal rows")

    def approve_proposal(self, proposal_id):
        self.approve_proposal_calls += 1
        raise AssertionError("readiness must not approve proposals")

    def get_approved_proposals(self):
        self.get_approved_proposals_calls += 1
        raise AssertionError("readiness must not read proposal rows")


class _NeverCalledSignalProvider:
    def __init__(self):
        self.calls = 0

    def __call__(self, *, pair):
        self.calls += 1
        raise AssertionError("readiness must not request a signal")


class _NeverCalledBoundExecutor:
    """A correctly bound executor: keyword-only, endpoint and paths bound."""

    def __init__(
        self,
        base_url,
        trade_state_db_path,
        drawdown_db_path,
        start_of_day_nav_db_path,
        approval_db_path,
    ):
        self.base_url = base_url
        self.trade_state_db_path = trade_state_db_path
        self.drawdown_db_path = drawdown_db_path
        self.start_of_day_nav_db_path = start_of_day_nav_db_path
        self.approval_db_path = approval_db_path
        self.calls = 0

    def __call__(self, *, proposal, raw_stop_loss_price):
        self.calls += 1
        raise AssertionError("readiness must not execute a trade")


class _PositionalBoundExecutor(_NeverCalledBoundExecutor):
    """Same attributes, but the two parameters are not keyword-only."""

    def __call__(self, proposal, raw_stop_loss_price):
        self.calls += 1
        raise AssertionError("readiness must not execute a trade")


def _safe_dependencies(tmp_path):
    """A practice-safe dependency set with consistent absolute paths."""
    trade_state_db_path = str(tmp_path / "dry_run_sustained.db")
    drawdown_db_path = str(tmp_path / "drawdown_high_water.db")
    start_of_day_nav_db_path = str(tmp_path / "start_of_day_nav.db")
    approval_db_path = str(tmp_path / "proposal_approvals.db")

    return {
        "broker": _NeverCalledBroker(PRACTICE_BASE_URL),
        "state_manager": _NeverCalledStateManager(trade_state_db_path),
        "signal_provider": _NeverCalledSignalProvider(),
        "proposal_queue": _NeverCalledProposalQueue(approval_db_path),
        "executor": _NeverCalledBoundExecutor(
            base_url=PRACTICE_BASE_URL,
            trade_state_db_path=trade_state_db_path,
            drawdown_db_path=drawdown_db_path,
            start_of_day_nav_db_path=start_of_day_nav_db_path,
            approval_db_path=approval_db_path,
        ),
        "config": {
            "api_key": SENTINEL_API_KEY,
            "account_id": SENTINEL_ACCOUNT_ID,
            "base_url": PRACTICE_BASE_URL,
            "trade_state_db_path": trade_state_db_path,
            "drawdown_db_path": drawdown_db_path,
            "start_of_day_nav_db_path": start_of_day_nav_db_path,
            "approval_db_path": approval_db_path,
        },
    }


def _assert_no_collaborator_calls(dependencies, label=""):
    prefix = f"[{label}] " if label else ""
    broker = dependencies["broker"]
    state_manager = dependencies["state_manager"]
    proposal_queue = dependencies["proposal_queue"]
    signal_provider = dependencies["signal_provider"]
    executor = dependencies["executor"]

    assert broker.get_open_positions_calls == 0, (
        f"{prefix}readiness must not read broker positions"
    )
    assert broker.get_quote_calls == 0, (
        f"{prefix}readiness must not request a quote"
    )
    assert state_manager.get_all_trades_calls == 0, (
        f"{prefix}readiness must not read the trade ledger"
    )
    assert proposal_queue.add_proposals_calls == 0, (
        f"{prefix}readiness must not write proposal rows"
    )
    assert proposal_queue.approve_proposal_calls == 0, (
        f"{prefix}readiness must not approve proposals"
    )
    assert proposal_queue.get_approved_proposals_calls == 0, (
        f"{prefix}readiness must not read proposal rows"
    )
    assert signal_provider.calls == 0, (
        f"{prefix}readiness must not request a signal"
    )
    assert executor.calls == 0, (
        f"{prefix}readiness must not execute a trade"
    )


def _assert_credentials_redacted(result, label=""):
    prefix = f"[{label}] " if label else ""
    rendered = repr(result)
    assert SENTINEL_API_KEY not in rendered, (
        f"{prefix}the readiness result must never carry the api key"
    )
    assert SENTINEL_ACCOUNT_ID not in rendered, (
        f"{prefix}the readiness result must never carry the account id"
    )


def test_check_readiness_accepts_safe_practice_wiring_without_invoking_anything(
    tmp_path,
    monkeypatch,
):
    wiring = importlib.import_module("autonomy_usdcad_wiring")

    # A live endpoint in the environment must be ignored entirely: the
    # verdict comes from the supplied configuration and object
    # properties, never from a configurable endpoint.
    monkeypatch.setenv("OANDA_BASE_URL", LIVE_BASE_URL)

    dependencies = _safe_dependencies(tmp_path)

    result = wiring.check_readiness(dependencies)

    assert isinstance(result, dict), (
        "check_readiness must return a structured result mapping"
    )
    assert set(result) == {"ready", "checks", "failures"}, (
        f"got top-level keys {sorted(result)!r}"
    )
    assert result["ready"] is True, (
        f"safe practice wiring must be ready; failures {result['failures']!r}"
    )
    assert result["failures"] == [], (
        f"got failures {result['failures']!r}"
    )
    assert isinstance(result["checks"], list), (
        f"got checks of type {type(result['checks'])!r}"
    )
    assert result["checks"], "checks must not be empty"

    names = []
    for check in result["checks"]:
        assert isinstance(check, dict), (
            f"each check must be a mapping; got {type(check)!r}"
        )
        assert set(check) == {"name", "passed", "detail"}, (
            f"each check must carry exactly name/passed/detail;"
            f" got {sorted(check)!r}"
        )
        assert isinstance(check["name"], str)
        assert isinstance(check["passed"], bool)
        assert isinstance(check["detail"], str)
        assert check["passed"] is True, (
            f"check {check['name']!r} must pass on safe wiring;"
            f" detail {check['detail']!r}"
        )
        names.append(check["name"])

    assert len(names) == len(set(names)), (
        f"check names must be unique; got {names!r}"
    )
    assert set(names) == REQUIRED_CHECK_NAMES, (
        "the readiness gate must run exactly the required checks;"
        f" missing {sorted(REQUIRED_CHECK_NAMES - set(names))!r},"
        f" unexpected {sorted(set(names) - REQUIRED_CHECK_NAMES)!r}"
    )

    _assert_credentials_redacted(result)
    _assert_no_collaborator_calls(dependencies)


@pytest.mark.parametrize(
    "case_name, expected_check",
    [
        ("missing_api_key", "credentials_present"),
        ("missing_account_id", "credentials_present"),
        ("live_config_endpoint", "configured_practice_endpoint"),
        ("live_broker_endpoint", "broker_practice_endpoint"),
        ("live_executor_endpoint", "executor_practice_endpoint"),
        ("default_trade_state_path", "database_paths_absolute"),
        ("relative_approval_path", "database_paths_absolute"),
        ("state_manager_path_mismatch", "state_manager_trade_state_path"),
        ("proposal_queue_path_mismatch", "proposal_queue_approval_path"),
        ("executor_trade_state_path_mismatch", "executor_database_paths"),
        ("executor_approval_path_mismatch", "executor_database_paths"),
        ("invalid_executor_interface", "executor_interface"),
    ],
)
def test_check_readiness_blocks_unsafe_or_inconsistent_wiring(
    case_name,
    expected_check,
    tmp_path,
    monkeypatch,
):
    wiring = importlib.import_module("autonomy_usdcad_wiring")

    monkeypatch.setenv("OANDA_BASE_URL", LIVE_BASE_URL)

    dependencies = _safe_dependencies(tmp_path)
    config = dependencies["config"]
    other_path = str(tmp_path / "elsewhere" / "other.db")

    if case_name == "missing_api_key":
        config["api_key"] = " "
    elif case_name == "missing_account_id":
        config["account_id"] = ""
    elif case_name == "live_config_endpoint":
        config["base_url"] = LIVE_BASE_URL
    elif case_name == "live_broker_endpoint":
        dependencies["broker"].base_url = LIVE_BASE_URL
    elif case_name == "live_executor_endpoint":
        dependencies["executor"].base_url = LIVE_BASE_URL
    elif case_name == "default_trade_state_path":
        config["trade_state_db_path"] = "trade_state.db"
        dependencies["state_manager"].db_path = "trade_state.db"
        dependencies["executor"].trade_state_db_path = "trade_state.db"
    elif case_name == "relative_approval_path":
        config["approval_db_path"] = "proposal_approvals.db"
        dependencies["proposal_queue"].db_path = "proposal_approvals.db"
        dependencies["executor"].approval_db_path = "proposal_approvals.db"
    elif case_name == "state_manager_path_mismatch":
        dependencies["state_manager"].db_path = other_path
    elif case_name == "proposal_queue_path_mismatch":
        dependencies["proposal_queue"].db_path = other_path
    elif case_name == "executor_trade_state_path_mismatch":
        dependencies["executor"].trade_state_db_path = other_path
    elif case_name == "executor_approval_path_mismatch":
        dependencies["executor"].approval_db_path = other_path
    elif case_name == "invalid_executor_interface":
        dependencies["executor"] = _PositionalBoundExecutor(
            base_url=config["base_url"],
            trade_state_db_path=config["trade_state_db_path"],
            drawdown_db_path=config["drawdown_db_path"],
            start_of_day_nav_db_path=config["start_of_day_nav_db_path"],
            approval_db_path=config["approval_db_path"],
        )
    else:
        raise AssertionError(f"unhandled case {case_name!r}")

    result = wiring.check_readiness(dependencies)

    assert isinstance(result, dict), (
        f"[{case_name}] check_readiness must return a result mapping"
    )
    assert set(result) == {"ready", "checks", "failures"}, (
        f"[{case_name}] got top-level keys {sorted(result)!r}"
    )
    assert result["ready"] is False, (
        f"[{case_name}] unsafe wiring must not be ready"
    )
    assert expected_check in result["failures"], (
        f"[{case_name}] expected {expected_check!r} in failures;"
        f" got {result['failures']!r}"
    )

    for failure in result["failures"]:
        assert isinstance(failure, str), (
            f"[{case_name}] failures must be check names; got {failure!r}"
        )
        assert failure in REQUIRED_CHECK_NAMES, (
            f"[{case_name}] failures must carry check names, not values;"
            f" got {failure!r}"
        )

    matching = [
        check
        for check in result["checks"]
        if check["name"] == expected_check
    ]
    assert len(matching) == 1, (
        f"[{case_name}] expected exactly one {expected_check!r} check;"
        f" got {len(matching)}"
    )
    matching_check = matching[0]
    assert matching_check["passed"] is False, (
        f"[{case_name}] {expected_check!r} must fail"
    )
    assert isinstance(matching_check["detail"], str), (
        f"[{case_name}] got detail of type {type(matching_check['detail'])!r}"
    )
    assert matching_check["detail"].strip(), (
        f"[{case_name}] {expected_check!r} must explain the failure"
    )

    _assert_credentials_redacted(result, case_name)
    _assert_no_collaborator_calls(dependencies, case_name)


def test_build_dependencies_constructs_minimal_practice_wiring(
    tmp_path,
    monkeypatch,
):
    """Assemble a ready practice-only wiring; nothing real is reached."""

    wiring = importlib.import_module("autonomy_usdcad_wiring")

    broker_kwargs = []
    provider_kwargs = []
    controller_calls = []
    controller_result = object()

    def _fail(*args, **kwargs):
        raise AssertionError("wiring must not call a collaborator")

    class _Strategy:
        recommend_strategy = staticmethod(_fail)

    class _AnalysisService:
        analyze_market_context = _fail

    def _make_broker(**kwargs):
        broker_kwargs.append(dict(kwargs))
        return _NeverCalledBroker(kwargs.get("base_url"))

    def _make_state_manager(**kwargs):
        return _NeverCalledStateManager(kwargs["db_path"])

    def _make_proposal_queue(**kwargs):
        return _NeverCalledProposalQueue(kwargs["db_path"])

    def _make_provider(**kwargs):
        provider_kwargs.append(dict(kwargs))
        return _NeverCalledSignalProvider()

    def _record_controller(**kwargs):
        controller_calls.append(dict(kwargs))
        return controller_result

    # raising=False: these module attributes do not exist yet.
    for name, value in (
        ("OandaBroker", _make_broker),
        ("PersistentTradeStateManager", _make_state_manager),
        ("ProposalApprovalQueue", _make_proposal_queue),
        ("UsdCadSignalProvider", _make_provider),
        ("get_fx_intraday", _fail),
        ("build_market_context", _fail),
        ("MarketAnalysisService", lambda *a, **k: _AnalysisService()),
        ("StrategyRecommendationService", _Strategy),
        ("execute_reviewed_proposal_from_dashboard", _record_controller),
    ):
        monkeypatch.setattr(wiring, name, value, raising=False)

    dependencies = wiring.build_dependencies(
        api_key="demo-api-key",
        account_id="demo-account-id",
        repo_root=tmp_path,
    )

    expected_paths = {
        "trade_state_db_path": str(tmp_path / "dry_run_sustained.db"),
        "drawdown_db_path": str(tmp_path / "drawdown_high_water.db"),
        "start_of_day_nav_db_path": str(tmp_path / "start_of_day_nav.db"),
        "approval_db_path": str(tmp_path / "proposal_approvals.db"),
    }

    assert set(dependencies) == {
        "config", "broker", "state_manager",
        "signal_provider", "proposal_queue", "executor",
    }, f"got dependency keys {sorted(dependencies)!r}"

    config = dependencies["config"]
    assert config["base_url"] == PRACTICE_BASE_URL, (
        f"got configured base_url {config['base_url']!r}"
    )
    for key, path in expected_paths.items():
        assert config[key] == path, f"got {key} {config[key]!r}"

    # The readiness gate owns the endpoint, path and interface checks.
    readiness = wiring.check_readiness(dependencies)
    assert readiness["ready"] is True, (
        f"built wiring must be ready; failures {readiness['failures']!r}"
    )
    assert readiness["failures"] == []

    assert broker_kwargs == [{
        "api_key": "demo-api-key",
        "account_id": "demo-account-id",
        "base_url": PRACTICE_BASE_URL,
    }], f"got broker constructions {broker_kwargs!r}"

    assert len(provider_kwargs) == 1, (
        f"got {len(provider_kwargs)} signal provider construction(s)"
    )
    assert set(provider_kwargs[0]) == {
        "fetch_intraday", "build_context",
        "analysis_service", "recommend_strategy",
    }, f"got signal provider kwargs {sorted(provider_kwargs[0])!r}"

    assert controller_calls == [], (
        "build_dependencies must bind the executor, not execute a trade"
    )

    proposal = {"proposal_id": "PROP-AUTO-TEST"}
    result = dependencies["executor"](
        proposal=proposal,
        raw_stop_loss_price=1.40000,
    )

    assert result is controller_result, (
        "the executor must return the reviewed controller's result"
    )
    assert len(controller_calls) == 1, (
        f"got {len(controller_calls)} controller call(s)"
    )

    call = controller_calls[0]
    assert call["proposal"] is proposal, "the proposal must be forwarded"
    assert call["base_url"] == PRACTICE_BASE_URL, (
        f"got forwarded base_url {call['base_url']!r}"
    )
    for key, path in expected_paths.items():
        assert call[key] == path, f"got forwarded {key} {call[key]!r}"

    now_utc = call["now_utc"]
    assert isinstance(now_utc, datetime), f"got {type(now_utc)!r}"
    assert now_utc.tzinfo is not None, "now_utc must be timezone-aware"
    assert now_utc.utcoffset() == timedelta(0), (
        f"now_utc must be UTC; got offset {now_utc.utcoffset()!r}"
    )


def test_real_state_manager_exposes_configured_db_path(tmp_path):
    """check_readiness reads the public db_path, so it must exist."""

    from execution.persistent_trade_state_manager import (
        PersistentTradeStateManager,
    )

    db_path = str(tmp_path / "state.db")
    collaborator = PersistentTradeStateManager(db_path=db_path)
    try:
        assert collaborator.db_path == db_path
    finally:
        collaborator._conn.close()


def test_real_proposal_queue_exposes_configured_db_path(tmp_path):
    """check_readiness reads the public db_path, so it must exist."""

    from ai.proposal_approval_queue import ProposalApprovalQueue

    db_path = str(tmp_path / "approvals.db")
    collaborator = ProposalApprovalQueue(db_path=db_path)
    try:
        assert collaborator.db_path == db_path
    finally:
        collaborator._conn.close()


def test_real_oanda_broker_exposes_configured_base_url():
    """check_readiness reads the public base_url, so it must exist."""

    from brokers.oanda_broker import OandaBroker

    base_url = "https://api-fxpractice.oanda.com"

    broker = OandaBroker(
        api_key="test-key",
        account_id="test-account",
        base_url=base_url,
    )

    assert broker.base_url == base_url
