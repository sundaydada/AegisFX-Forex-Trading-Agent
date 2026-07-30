"""Import-safe readiness gate for the autonomous USD/CAD cycle.

check_readiness inspects supplied configuration values and object
properties only. It reads no environment variable, constructs nothing,
opens no database, makes no network call, and never invokes a
collaborator. An unready wiring is reported as data, never raised, and
credentials never appear in the result.

build_dependencies composes the practice-only dependency bundle. It is
the only function here that constructs anything; importing this module
binds collaborators and defines functions, but opens no database, reads
no environment variable, and makes no network call.
"""

import inspect
import os
from datetime import datetime, timezone
from pathlib import Path

from ai.market_analysis_service import MarketAnalysisService
from ai.proposal_approval_queue import ProposalApprovalQueue
from ai.strategy_recommendation_service import StrategyRecommendationService
from autonomy_usdcad_signal import UsdCadSignalProvider
from brokers.oanda_broker import OandaBroker
from dashboard.reviewed_execution_controller import (
    execute_reviewed_proposal_from_dashboard,
)
from execution.persistent_trade_state_manager import (
    PersistentTradeStateManager,
)
from market_data.alpha_vantage_price_feed import get_fx_intraday
from market_data.market_context import build_market_context

PRACTICE_BASE_URL = "https://api-fxpractice.oanda.com"

_DEFAULT_TRADE_STATE_BASENAME = "trade_state.db"
_BARE_APPROVAL_PATH = "proposal_approvals.db"

_CONFIGURED_PATH_KEYS = (
    "trade_state_db_path",
    "drawdown_db_path",
    "start_of_day_nav_db_path",
    "approval_db_path",
)


def _record(checks, name, passed, detail):
    checks.append(
        {
            "name": name,
            "passed": bool(passed),
            "detail": detail,
        }
    )


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _has_callable(obj, name) -> bool:
    return callable(getattr(obj, name, None))


def check_readiness(dependencies: dict) -> dict:
    """Inspect autonomous wiring without invoking operational collaborators."""

    checks = []

    mapping = dependencies if isinstance(dependencies, dict) else {}
    config = mapping.get("config")
    if not isinstance(config, dict):
        config = {}

    broker = mapping.get("broker")
    state_manager = mapping.get("state_manager")
    signal_provider = mapping.get("signal_provider")
    proposal_queue = mapping.get("proposal_queue")
    executor = mapping.get("executor")

    # --- credentials: presence only, never values ---
    api_key_present = _is_nonempty_str(config.get("api_key"))
    account_id_present = _is_nonempty_str(config.get("account_id"))
    _record(
        checks,
        "credentials_present",
        api_key_present and account_id_present,
        f"api_key_present={api_key_present};"
        f" account_id_present={account_id_present}",
    )

    # --- endpoints: exact practice match, from supplied values only ---
    configured_endpoint = config.get("base_url")
    broker_endpoint = getattr(broker, "base_url", None)
    executor_endpoint = getattr(executor, "base_url", None)

    configured_is_practice = configured_endpoint == PRACTICE_BASE_URL
    broker_is_practice = broker_endpoint == PRACTICE_BASE_URL
    executor_is_practice = executor_endpoint == PRACTICE_BASE_URL

    _record(
        checks,
        "configured_practice_endpoint",
        configured_is_practice,
        "the configured base_url must be exactly the OANDA practice"
        f" endpoint: {configured_is_practice}",
    )
    _record(
        checks,
        "broker_practice_endpoint",
        broker_is_practice,
        "the broker base_url must be exactly the OANDA practice"
        f" endpoint: {broker_is_practice}",
    )
    _record(
        checks,
        "executor_practice_endpoint",
        executor_is_practice,
        "the bound executor base_url must be exactly the OANDA practice"
        f" endpoint: {executor_is_practice}",
    )

    # --- database paths: shape only, never contents ---
    path_values = [config.get(key) for key in _CONFIGURED_PATH_KEYS]
    all_strings = all(_is_nonempty_str(value) for value in path_values)
    all_absolute = all_strings and all(
        os.path.isabs(value) for value in path_values
    )
    parents_exist = all_absolute and all(
        os.path.isdir(os.path.dirname(value)) for value in path_values
    )

    trade_state_value = config.get("trade_state_db_path")
    approval_value = config.get("approval_db_path")
    not_default_trade_state = (
        _is_nonempty_str(trade_state_value)
        and os.path.basename(trade_state_value)
        != _DEFAULT_TRADE_STATE_BASENAME
    )
    not_bare_approval = (
        _is_nonempty_str(approval_value)
        and approval_value != _BARE_APPROVAL_PATH
    )

    paths_ok = (
        all_strings
        and all_absolute
        and parents_exist
        and not_default_trade_state
        and not_bare_approval
    )
    _record(
        checks,
        "database_paths_absolute",
        paths_ok,
        "all four database paths must be non-empty absolute paths with an"
        " existing parent directory, must not use the default trade-state"
        f" filename, and must not be a bare approval path: {paths_ok}",
    )

    # --- path consistency: one ledger, one approval queue ---
    state_manager_path_ok = (
        _is_nonempty_str(trade_state_value)
        and getattr(state_manager, "db_path", None) == trade_state_value
    )
    _record(
        checks,
        "state_manager_trade_state_path",
        state_manager_path_ok,
        "the state manager must use the configured trade-state database"
        f" path: {state_manager_path_ok}",
    )

    proposal_queue_path_ok = (
        _is_nonempty_str(approval_value)
        and getattr(proposal_queue, "db_path", None) == approval_value
    )
    _record(
        checks,
        "proposal_queue_approval_path",
        proposal_queue_path_ok,
        "the proposal queue must use the configured approval database"
        f" path: {proposal_queue_path_ok}",
    )

    executor_paths_ok = all(
        _is_nonempty_str(config.get(key))
        and getattr(executor, key, None) == config.get(key)
        for key in _CONFIGURED_PATH_KEYS
    )
    _record(
        checks,
        "executor_database_paths",
        executor_paths_ok,
        "the bound executor must carry the same four configured database"
        f" paths: {executor_paths_ok}",
    )

    # --- interfaces: attribute lookup only, never invocation ---
    broker_interface_ok = _has_callable(
        broker, "get_open_positions"
    ) and _has_callable(broker, "get_quote")
    _record(
        checks,
        "broker_interface",
        broker_interface_ok,
        "the broker must expose callable get_open_positions and get_quote:"
        f" {broker_interface_ok}",
    )

    state_manager_interface_ok = _has_callable(state_manager, "get_all_trades")
    _record(
        checks,
        "state_manager_interface",
        state_manager_interface_ok,
        "the state manager must expose a callable get_all_trades:"
        f" {state_manager_interface_ok}",
    )

    proposal_queue_interface_ok = all(
        _has_callable(proposal_queue, name)
        for name in (
            "add_proposals",
            "approve_proposal",
            "get_approved_proposals",
        )
    )
    _record(
        checks,
        "proposal_queue_interface",
        proposal_queue_interface_ok,
        "the proposal queue must expose callable add_proposals,"
        " approve_proposal and get_approved_proposals:"
        f" {proposal_queue_interface_ok}",
    )

    signal_provider_ok = callable(signal_provider)
    _record(
        checks,
        "signal_provider_callable",
        signal_provider_ok,
        f"the signal provider must be callable: {signal_provider_ok}",
    )

    # --- executor shape: read the signature, never call it ---
    executor_interface_ok = False
    try:
        signature = inspect.signature(executor)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        parameters = list(signature.parameters.values())
        executor_interface_ok = (
            len(parameters) == 2
            and all(
                parameter.kind is inspect.Parameter.KEYWORD_ONLY
                for parameter in parameters
            )
            and {parameter.name for parameter in parameters}
            == {"proposal", "raw_stop_loss_price"}
        )
    _record(
        checks,
        "executor_interface",
        executor_interface_ok,
        "the bound executor must accept exactly the keyword-only arguments"
        f" proposal and raw_stop_loss_price: {executor_interface_ok}",
    )

    # --- no live endpoint anywhere in the supplied wiring ---
    no_live_endpoint = all(
        isinstance(endpoint, str) and "fxtrade" not in endpoint.lower()
        for endpoint in (
            configured_endpoint,
            broker_endpoint,
            executor_endpoint,
        )
    )
    _record(
        checks,
        "no_live_endpoint",
        no_live_endpoint,
        "no configured, broker, or executor endpoint may reference a live"
        f" OANDA host: {no_live_endpoint}",
    )

    failures = [
        check["name"] for check in checks if check["passed"] is False
    ]

    return {
        "ready": not failures,
        "checks": checks,
        "failures": failures,
    }


def build_dependencies(
    *,
    api_key: str,
    account_id: str,
    repo_root=None,
) -> dict:
    """Construct the practice-only dependency bundle run_cycle needs.

    The endpoint is the module constant — never an argument, never an
    environment variable — so a live account is unreachable through this
    API. now_utc is taken inside the executor, so every call is stamped
    with its own time rather than with build time.
    """

    root = (
        Path(repo_root).resolve()
        if repo_root is not None
        else Path(__file__).resolve().parent
    )

    trade_state_db_path = str(root / "dry_run_sustained.db")
    drawdown_db_path = str(root / "drawdown_high_water.db")
    start_of_day_nav_db_path = str(root / "start_of_day_nav.db")
    approval_db_path = str(root / "proposal_approvals.db")

    config = {
        "api_key": api_key,
        "account_id": account_id,
        "base_url": PRACTICE_BASE_URL,
        "trade_state_db_path": trade_state_db_path,
        "drawdown_db_path": drawdown_db_path,
        "start_of_day_nav_db_path": start_of_day_nav_db_path,
        "approval_db_path": approval_db_path,
    }

    broker = OandaBroker(
        api_key=api_key,
        account_id=account_id,
        base_url=PRACTICE_BASE_URL,
    )

    state_manager = PersistentTradeStateManager(
        db_path=trade_state_db_path,
    )

    proposal_queue = ProposalApprovalQueue(
        db_path=approval_db_path,
    )

    analysis_service = MarketAnalysisService()

    signal_provider = UsdCadSignalProvider(
        fetch_intraday=get_fx_intraday,
        build_context=build_market_context,
        analysis_service=analysis_service,
        recommend_strategy=(
            StrategyRecommendationService.recommend_strategy
        ),
    )

    def executor(*, proposal, raw_stop_loss_price):
        return execute_reviewed_proposal_from_dashboard(
            proposal=proposal,
            raw_stop_loss_price=raw_stop_loss_price,
            api_key=api_key,
            account_id=account_id,
            base_url=PRACTICE_BASE_URL,
            trade_state_db_path=trade_state_db_path,
            drawdown_db_path=drawdown_db_path,
            start_of_day_nav_db_path=start_of_day_nav_db_path,
            approval_db_path=approval_db_path,
            max_currency_exposure=100.0,
            max_quote_age_seconds=60.0,
            now_utc=datetime.now(timezone.utc),
        )

    # The readiness gate inspects these attributes without calling it.
    executor.base_url = PRACTICE_BASE_URL
    executor.trade_state_db_path = trade_state_db_path
    executor.drawdown_db_path = drawdown_db_path
    executor.start_of_day_nav_db_path = start_of_day_nav_db_path
    executor.approval_db_path = approval_db_path

    return {
        "config": config,
        "broker": broker,
        "state_manager": state_manager,
        "signal_provider": signal_provider,
        "proposal_queue": proposal_queue,
        "executor": executor,
    }
