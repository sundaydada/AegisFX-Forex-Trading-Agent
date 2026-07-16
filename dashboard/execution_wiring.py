"""Import-safe dependency wiring for the operator-reviewed execution path.

build_reviewed_execution_wiring validates explicitly injected identity,
paths, and policy values, then constructs the complete dependency set
for execute_reviewed_proposal_action: one OandaBroker serving as both
broker and quote provider, the orchestrator bound to it, the persistent
trade-state manager, the persistent high-water drawdown provider, the
proposal execution bridge, and a mark-executed closure bound to the
injected approval database.

Importing this module performs no construction, reads no environment
variables, opens no database, and makes no network call. The returned
bundle owns the state manager and drawdown provider and releases both
via close(). No runtime evidence (quote, snapshot, stop, drawdown,
units) is fetched, calculated, or defaulted here — the runtime-input
resolver acquires all of it fresh at execution time.
"""

import math
import os
from datetime import datetime
from numbers import Real
from types import MappingProxyType
from typing import Mapping

from ai.proposal_approval_queue import ProposalApprovalQueue
from ai.proposal_execution_bridge import ProposalExecutionBridge
from brokers.oanda_broker import OandaBroker
from execution.persistent_drawdown_provider import (
    PersistentHighWaterDrawdownProvider,
)
from execution.persistent_start_of_day_nav_provider import (
    PersistentStartOfDayNavProvider,
)
from execution.persistent_trade_state_manager import (
    PersistentTradeStateManager,
)
from execution.trade_orchestrator import TradeOrchestrator


def _validated_identity(name: str, value) -> str:
    if (
        isinstance(value, bool)
        or not isinstance(value, str)
        or not value.strip()
    ):
        raise ValueError(
            f"Invalid {name} evidence: a nonempty string is required"
        )
    return value.strip()


def _validated_db_path(name: str, value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, os.PathLike)):
        raise ValueError(
            f"Invalid {name} evidence: a database path is required"
        )
    path_text = os.fspath(value)
    if not isinstance(path_text, str) or not path_text.strip():
        raise ValueError(
            f"Invalid {name} evidence: a database path is required"
        )
    return path_text


def _validated_positive_policy(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(
            f"Invalid {name} evidence: a finite positive number is required"
        )
    numeric = float(value)
    if not math.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(
            f"Invalid {name} evidence: a finite positive number is required"
        )
    return numeric


class ReviewedExecutionWiring:
    """Owned dependency bundle for the reviewed-execution action."""

    def __init__(
        self,
        *,
        action_kwargs,
        state_manager,
        drawdown_provider,
        start_of_day_nav_provider=None,
    ):
        self.action_kwargs = action_kwargs
        self._state_manager = state_manager
        self._drawdown_provider = drawdown_provider
        self._start_of_day_nav_provider = start_of_day_nav_provider
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._state_manager.close()
        finally:
            try:
                self._drawdown_provider.close()
            finally:
                if self._start_of_day_nav_provider is not None:
                    self._start_of_day_nav_provider.close()


def build_reviewed_execution_wiring(
    *,
    api_key,
    account_id,
    base_url,
    trade_state_db_path,
    drawdown_db_path,
    approval_db_path,
    max_currency_exposure,
    max_quote_age_seconds,
    now_utc,
    start_of_day_nav_db_path=None,
) -> ReviewedExecutionWiring:
    validated_api_key = _validated_identity("API key", api_key)
    validated_account_id = _validated_identity(
        "account identity",
        account_id,
    )
    validated_base_url = _validated_identity("base URL", base_url)
    validated_trade_state_db_path = _validated_db_path(
        "trade_state_db_path",
        trade_state_db_path,
    )
    validated_drawdown_db_path = _validated_db_path(
        "drawdown_db_path",
        drawdown_db_path,
    )
    validated_approval_db_path = _validated_db_path(
        "approval_db_path",
        approval_db_path,
    )
    validated_start_of_day_nav_db_path = None
    if start_of_day_nav_db_path is not None:
        validated_start_of_day_nav_db_path = _validated_db_path(
            "start_of_day_nav_db_path",
            start_of_day_nav_db_path,
        )
    validated_max_currency_exposure = _validated_positive_policy(
        "max_currency_exposure",
        max_currency_exposure,
    )
    validated_max_quote_age_seconds = _validated_positive_policy(
        "max_quote_age_seconds",
        max_quote_age_seconds,
    )
    if (
        not isinstance(now_utc, datetime)
        or now_utc.tzinfo is None
        or now_utc.utcoffset() is None
    ):
        raise ValueError(
            "Invalid now_utc evidence: a timezone-aware datetime is required"
        )

    broker = OandaBroker(
        api_key=validated_api_key,
        account_id=validated_account_id,
        base_url=validated_base_url,
    )
    orchestrator = None
    if validated_start_of_day_nav_db_path is None:
        orchestrator = TradeOrchestrator(broker)

    state_manager = PersistentTradeStateManager(
        db_path=validated_trade_state_db_path
    )
    drawdown_provider = None
    start_of_day_nav_provider = None
    try:
        drawdown_provider = PersistentHighWaterDrawdownProvider(
            db_path=validated_drawdown_db_path,
            account_id=validated_account_id,
        )
        if validated_start_of_day_nav_db_path is not None:
            def start_of_day_nav_clock():
                return now_utc

            start_of_day_nav_provider = PersistentStartOfDayNavProvider(
                db_path=validated_start_of_day_nav_db_path,
                account_id=validated_account_id,
                clock=start_of_day_nav_clock,
            )
            orchestrator = TradeOrchestrator(
                broker,
                start_of_day_nav_provider=start_of_day_nav_provider,
            )

        def mark_executed(proposal_id):
            queue = ProposalApprovalQueue(db_path=validated_approval_db_path)
            try:
                return queue.mark_executed(proposal_id)
            finally:
                queue.close()

        action_kwargs: Mapping = MappingProxyType({
            "broker": broker,
            "quote_provider": broker,
            "drawdown_provider": drawdown_provider,
            "bridge_execute": (
                ProposalExecutionBridge.execute_approved_proposal
            ),
            "mark_executed": mark_executed,
            "now_utc": now_utc,
            "max_quote_age_seconds": validated_max_quote_age_seconds,
            "max_currency_exposure": validated_max_currency_exposure,
            "orchestrator": orchestrator,
            "state_manager": state_manager,
        })
    except BaseException:
        try:
            state_manager.close()
        except BaseException:
            pass

        if drawdown_provider is not None:
            try:
                drawdown_provider.close()
            except BaseException:
                pass

        if start_of_day_nav_provider is not None:
            try:
                start_of_day_nav_provider.close()
            except BaseException:
                pass

        raise

    return ReviewedExecutionWiring(
        action_kwargs=action_kwargs,
        state_manager=state_manager,
        drawdown_provider=drawdown_provider,
        start_of_day_nav_provider=start_of_day_nav_provider,
    )
