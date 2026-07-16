import logging
import math
from datetime import datetime, timezone
from numbers import Real
from typing import Dict, List

from brokers.broker_interface import BrokerInterface
from execution.position_netting import net_position
from execution.portfolio_risk_evaluator import (
    PortfolioRiskEvaluator,
    evaluate_risk_at_stop,
)
from execution.trade_state_manager import TradeStateManager
from execution.trading_control import is_trading_enabled

PENDING_TIMEOUT_SECONDS = 60

_MONETARY_EVIDENCE_FIELDS = {
    "nav",
    "account_currency",
    "risk_fraction",
    "risk_budget_amount",
    "loss_per_unit_at_stop",
}

logger = logging.getLogger("aegisfx.orchestrator")


def _positive_finite_real(name: str, value, *, maximum=None) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(
            f"Invalid monetary risk evidence: {name} must be a real number"
        )

    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(
            f"Invalid monetary risk evidence: {name} must be finite and positive"
        )
    if maximum is not None and normalized > maximum:
        raise ValueError(
            f"Invalid monetary risk evidence: {name} must not exceed {maximum}"
        )
    return normalized


def _currency_pair_members(pair, *, context: str) -> set[str]:
    if not isinstance(pair, str):
        raise ValueError(f"{context} has missing or malformed currency-pair evidence")

    normalized = pair.strip().upper()
    currencies = normalized.split("/")
    if (
        len(currencies) != 2
        or any(len(currency) != 3 for currency in currencies)
        or any(not currency.isalpha() for currency in currencies)
    ):
        raise ValueError(f"{context} has missing or malformed currency-pair evidence")
    return set(currencies)


def _aggregate_existing_monetary_risk(
    trades: List[Dict],
    proposed_currencies: set[str],
) -> tuple[float, float]:
    portfolio_risk = 0.0
    same_currency_risk = 0.0

    for trade in trades:
        status = trade.get("status")
        if status is None:
            execution_status = trade.get("execution_status")
            if execution_status == "Filled":
                status = "FILLED"
            elif execution_status == "Pending":
                status = "PENDING"

        if status == "CLOSED":
            continue
        if status == "PENDING":
            raise ValueError(
                "Unresolved PENDING trade has uncertain monetary risk"
            )
        if status != "FILLED":
            continue

        trade_currencies = _currency_pair_members(
            trade.get("currency_pair"),
            context="Open FILLED trade",
        )
        if "risk_at_stop_amount" not in trade:
            raise ValueError(
                "Open FILLED trade is missing monetary risk evidence"
            )
        try:
            trade_risk = _positive_finite_real(
                "risk_at_stop_amount",
                trade["risk_at_stop_amount"],
            )
        except ValueError as exc:
            raise ValueError(
                "Open FILLED trade has invalid monetary risk evidence"
            ) from exc

        portfolio_risk += trade_risk
        if trade_currencies & proposed_currencies:
            same_currency_risk += trade_risk

        if not math.isfinite(portfolio_risk) or not math.isfinite(
            same_currency_risk
        ):
            raise ValueError(
                "Existing monetary risk aggregation must remain finite"
            )

    return portfolio_risk, same_currency_risk


class TradeOrchestrator:
    """
    Enforces approval -> execution -> state persistence flow.
    This is the ONLY valid trade execution entry point.
    """

    def __init__(self, broker: BrokerInterface):
        self._broker = broker
        self._trade_timestamps = []
        self._max_trades_per_minute = 100
        self._failure_threshold = 0.5
        self._min_trades_for_check = 3
        self._metrics = {
            "total_trades": 0,
            "successful_trades": 0,
            "failed_trades": 0,
            "timeout_trades": 0,
            "exception_trades": 0,
        }

    def _trigger_alert(self, message: str) -> None:
        logger.warning({"event": "alert", "message": message})
        print(f"ALERT: {message}")

    def _check_failure_rate(self) -> None:
        total = self._metrics["total_trades"]
        if total == 0:
            return

        failure_rate = self._metrics["failed_trades"] / total
        if failure_rate > self._failure_threshold:
            self._trigger_alert(
                f"High failure rate detected: {failure_rate:.1%} "
                f"({self._metrics['failed_trades']}/{total})"
            )

    def get_metrics(self) -> Dict:
        return dict(self._metrics)

    def process_trade(
        self,
        state_manager: TradeStateManager,
        request_id: str,
        proposed_trade: Dict,
        max_currency_exposure: float,
    ) -> Dict:
        """
        Returns structured result:
        {
            "approval_status": str,
            "reason": str,
            "execution_result": Dict | None
        }
        """

        total = self._metrics["total_trades"]
        cb_active = (total >= self._min_trades_for_check and
                     (self._metrics["failed_trades"] / total) > self._failure_threshold) if total > 0 else False
        print("DEBUG: Circuit breaker state:", cb_active, "| Metrics:", self._metrics)

        logger.info({
            "event": "trade_received",
            "request_id": request_id,
            "currency_pair": proposed_trade.get("currency_pair"),
            "direction": proposed_trade.get("direction"),
            "position_size": proposed_trade.get("approved_position_size"),
        })

        # Idempotency check
        if state_manager.has_processed(request_id):
            print(f"DEBUG: Idempotency hit for {request_id}")
            logger.info({"event": "idempotency_hit", "request_id": request_id})
            return state_manager.get_processed_result(request_id)

        current_trades = state_manager.get_all_trades()

        # Crash recovery check — prevent duplicate broker execution
        for trade in current_trades:
            if trade.get("request_id") == request_id and trade.get("status") != "PENDING":
                logger.info({"event": "crash_recovery_hit", "request_id": request_id})
                return state_manager.get_processed_result(request_id)

        # Circuit breaker check
        total = self._metrics["total_trades"]
        if total >= self._min_trades_for_check:
            failure_rate = self._metrics["failed_trades"] / total
            if failure_rate > self._failure_threshold:
                self._trigger_alert("Circuit breaker activated")
                logger.warning({
                    "event": "circuit_breaker_triggered",
                    "request_id": request_id,
                    "failure_rate": round(failure_rate, 3),
                })
                return {
                    "approval_status": "Rejected",
                    "reason": "Circuit breaker triggered",
                    "execution_result": None,
                }

        # Rate limit check
        now = datetime.now(timezone.utc)
        self._trade_timestamps = [
            ts for ts in self._trade_timestamps
            if (now - ts).total_seconds() <= 60
        ]

        if len(self._trade_timestamps) >= self._max_trades_per_minute:
            logger.warning({
                "event": "rate_limit_exceeded",
                "request_id": request_id,
                "trades_in_window": len(self._trade_timestamps),
            })
            return {
                "approval_status": "Rejected",
                "reason": "Rate limit exceeded",
                "execution_result": None,
            }

        self._trade_timestamps.append(now)
        self._metrics["total_trades"] += 1

        # Step 1: Position netting (BEFORE risk evaluation)
        remaining_size, closed_count = net_position(state_manager, proposed_trade)

        logger.info({
            "event": "position_netting",
            "request_id": request_id,
            "original_size": proposed_trade.get("approved_position_size"),
            "remaining_size": remaining_size,
            "closed_count": closed_count,
        })

        if remaining_size == 0.0:
            result = {
                "approval_status": "Netted",
                "reason": f"Fully netted against {closed_count} existing position(s)",
                "execution_result": None,
            }
            state_manager.record_processed_result(request_id, result)
            self._metrics["successful_trades"] += 1
            logger.info({"event": "trade_finalized", "request_id": request_id, "status": "NETTED"})
            return result

        # Update proposed trade with netted size
        proposed_trade = dict(proposed_trade)
        proposed_trade["approved_position_size"] = remaining_size

        # Step 2: Risk Evaluation (on reduced size)
        current_trades = state_manager.get_all_trades()
        supplied_evidence_fields = (
            _MONETARY_EVIDENCE_FIELDS & proposed_trade.keys()
        )
        uses_monetary_risk = bool(supplied_evidence_fields)

        if uses_monetary_risk:
            missing_evidence = sorted(
                _MONETARY_EVIDENCE_FIELDS - proposed_trade.keys()
            )
            if missing_evidence:
                risk_decision = {
                    "approval_status": "Rejected",
                    "reason": (
                        "Missing monetary risk evidence: "
                        + ", ".join(missing_evidence)
                    ),
                }
            elif type(remaining_size) is not int or remaining_size <= 0:
                risk_decision = {
                    "approval_status": "Rejected",
                    "reason": (
                        "Monetary risk evidence requires an exact positive "
                        "integer position size"
                    ),
                }
            else:
                try:
                    validated_nav = _positive_finite_real(
                        "nav",
                        proposed_trade["nav"],
                    )
                    validated_risk_fraction = _positive_finite_real(
                        "risk_fraction",
                        proposed_trade["risk_fraction"],
                        maximum=1.0,
                    )
                    validated_risk_budget = _positive_finite_real(
                        "risk_budget_amount",
                        proposed_trade["risk_budget_amount"],
                    )
                    validated_loss_per_unit = _positive_finite_real(
                        "loss_per_unit_at_stop",
                        proposed_trade["loss_per_unit_at_stop"],
                    )
                    account_currency = proposed_trade["account_currency"]
                    if (
                        not isinstance(account_currency, str)
                        or len(account_currency.strip()) != 3
                        or not account_currency.strip().isalpha()
                    ):
                        raise ValueError(
                            "Invalid monetary risk evidence: account_currency "
                            "must be a three-letter currency code"
                        )
                    validated_account_currency = account_currency.strip().upper()
                    proposed_currencies = _currency_pair_members(
                        proposed_trade.get("currency_pair"),
                        context="Proposed trade",
                    )
                    risk_at_stop_amount = _positive_finite_real(
                        "risk_at_stop_amount",
                        remaining_size * validated_loss_per_unit,
                    )
                    (
                        existing_portfolio_risk_amount,
                        existing_same_currency_risk_amount,
                    ) = _aggregate_existing_monetary_risk(
                        current_trades,
                        proposed_currencies,
                    )
                except ValueError as exc:
                    risk_decision = {
                        "approval_status": "Rejected",
                        "reason": str(exc),
                    }
                else:
                    monetary_decision = evaluate_risk_at_stop(
                        nav=validated_nav,
                        proposed_risk_amount=risk_at_stop_amount,
                        existing_portfolio_risk_amount=(
                            existing_portfolio_risk_amount
                        ),
                        existing_same_currency_risk_amount=(
                            existing_same_currency_risk_amount
                        ),
                    )
                    risk_decision = {
                        "approval_status": monetary_decision.approval_status,
                        "reason": monetary_decision.reason,
                    }
                    proposed_trade.update({
                        "nav": validated_nav,
                        "account_currency": validated_account_currency,
                        "risk_fraction": validated_risk_fraction,
                        "risk_budget_amount": validated_risk_budget,
                        "loss_per_unit_at_stop": validated_loss_per_unit,
                        "risk_at_stop_amount": risk_at_stop_amount,
                    })
        else:
            risk_decision = PortfolioRiskEvaluator.evaluate_trade(
                current_trades=current_trades,
                proposed_trade=proposed_trade,
                max_currency_exposure=max_currency_exposure,
            )

        logger.info({
            "event": "risk_evaluation",
            "request_id": request_id,
            "approval_status": risk_decision["approval_status"],
            "reason": risk_decision["reason"],
        })

        if risk_decision["approval_status"] == "Rejected":
            result = {
                "approval_status": "Rejected",
                "reason": risk_decision["reason"],
                "execution_result": None,
            }
            state_manager.record_processed_result(request_id, result)
            return result

        # Step 3: Record pending trade before broker call
        pending_trade = {
            "request_id": request_id,
            "currency_pair": proposed_trade["currency_pair"],
            "direction": proposed_trade["direction"],
            "position_size": proposed_trade["approved_position_size"],
            "status": "PENDING",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if uses_monetary_risk:
            pending_trade.update({
                "nav": proposed_trade["nav"],
                "account_currency": proposed_trade["account_currency"],
                "risk_fraction": proposed_trade["risk_fraction"],
                "risk_budget_amount": proposed_trade["risk_budget_amount"],
                "loss_per_unit_at_stop": proposed_trade[
                    "loss_per_unit_at_stop"
                ],
                "risk_at_stop_amount": proposed_trade["risk_at_stop_amount"],
            })
        state_manager.record_trade(pending_trade)

        # Step 4: Check broker health before execution
        if hasattr(self._broker, 'health') and not self._broker.health.connected:
            logger.warning({
                "event": "broker_disconnected_block",
                "request_id": request_id,
            })

            execution_result = {
                "execution_status": "ERROR",
                "error_message": "Execution blocked: broker disconnected",
            }

            final_result = {
                "approval_status": "Failed",
                "reason": "Broker disconnected",
                "execution_result": execution_result,
            }

            state_manager.update_trade(request_id, execution_result, status="FAILED")
            state_manager.record_processed_result(request_id, final_result)

            self._metrics["failed_trades"] += 1
            self._metrics["exception_trades"] += 1

            logger.info({"event": "trade_finalized", "request_id": request_id, "status": "FAILED"})
            return final_result

        # Step 5: Check operator trading control
        if not is_trading_enabled():
            logger.warning({
                "event": "trading_disabled_block",
                "request_id": request_id,
            })

            execution_result = {
                "execution_status": "BLOCKED",
                "error_message": "Trading disabled by operator",
            }

            final_result = {
                "approval_status": "Failed",
                "reason": "Trading disabled by operator",
                "execution_result": execution_result,
            }

            state_manager.update_trade(request_id, execution_result, status="FAILED")
            state_manager.record_processed_result(request_id, final_result)

            self._metrics["failed_trades"] += 1

            logger.info({"event": "trade_finalized", "request_id": request_id, "status": "BLOCKED"})
            return final_result

        # Step 6: Execute Trade via broker
        order = {
            "currency_pair": proposed_trade["currency_pair"],
            "direction": proposed_trade["direction"],
            "position_size": proposed_trade["approved_position_size"],
            "stop_loss_price": proposed_trade["stop_loss_price"],
        }

        logger.info({"event": "executing_trade", "request_id": request_id})

        try:
            execution_result = self._broker.place_order(order)
        except Exception as e:
            logger.error({
                "event": "broker_exception",
                "request_id": request_id,
                "error_message": str(e),
            })

            execution_result = {
                "execution_status": "ERROR",
                "error_message": str(e),
            }

            final_result = {
                "approval_status": "Failed",
                "reason": "Execution error",
                "execution_result": execution_result,
            }

            state_manager.update_trade(request_id, execution_result, status="FAILED")
            state_manager.record_processed_result(request_id, final_result)

            self._metrics["failed_trades"] += 1
            self._metrics["exception_trades"] += 1

            self._trigger_alert(f"Execution error for request_id: {request_id}")
            self._check_failure_rate()

            logger.info({"event": "trade_finalized", "request_id": request_id, "status": "FAILED"})

            return final_result

        logger.info({
            "event": "broker_response",
            "request_id": request_id,
            "execution_status": execution_result.get("execution_status"),
            "error_message": execution_result.get("error_message"),
        })

        # Step 4: Map execution status to trade lifecycle status
        if execution_result["execution_status"] == "Filled":
            trade_status = "FILLED"
        else:
            trade_status = "FAILED"

        # Build final result before any state mutation
        final_result = {
            "approval_status": "Approved" if trade_status == "FILLED" else "Failed",
            "reason": risk_decision["reason"],
            "execution_result": execution_result,
        }

        # Step 5: Atomic Commit — update pending record, don't create new one
        state_manager.update_trade(request_id, execution_result, status=trade_status)
        state_manager.record_processed_result(request_id, final_result)

        if trade_status == "FILLED":
            self._metrics["successful_trades"] += 1
        else:
            self._metrics["failed_trades"] += 1

        self._check_failure_rate()

        logger.info({"event": "trade_finalized", "request_id": request_id, "status": trade_status})

        return final_result

    def reconcile_pending_trades(self, state_manager: TradeStateManager) -> None:
        """
        Startup recovery: resolve any trades stuck in PENDING status.
        Stale trades (beyond timeout) are marked FAILED without broker query.
        Recent trades are checked with the broker.
        """

        now = datetime.now(timezone.utc)
        all_trades = state_manager.get_all_trades()
        pending_trades = [t for t in all_trades if t.get("status") == "PENDING"]

        logger.info({"event": "reconcile_start", "pending_count": len(pending_trades)})

        for trade in pending_trades:
            request_id = trade.get("request_id")
            if not request_id:
                logger.warning({"event": "reconcile_skip", "reason": "no request_id"})
                continue

            # Check for stale pending trades
            created_at = trade.get("created_at")
            if created_at:
                created_time = datetime.fromisoformat(created_at)
                elapsed = (now - created_time).total_seconds()

                if elapsed > PENDING_TIMEOUT_SECONDS:
                    timeout_result = {"execution_status": "TIMEOUT"}
                    state_manager.update_trade(
                        request_id,
                        timeout_result,
                        status="FAILED",
                    )

                    if not state_manager.has_processed(request_id):
                        state_manager.record_processed_result(request_id, {
                            "approval_status": "Failed",
                            "reason": "Reconciled after restart",
                            "execution_result": timeout_result,
                        })

                    self._metrics["timeout_trades"] += 1
                    self._metrics["failed_trades"] += 1

                    logger.warning({
                        "event": "reconcile_timeout",
                        "request_id": request_id,
                        "elapsed_seconds": round(elapsed),
                    })
                    continue

            # Recent pending trade — query broker for actual status
            try:
                broker_response = self._broker.get_order_status(request_id)
            except Exception as e:
                logger.error({
                    "event": "reconcile_broker_error",
                    "request_id": request_id,
                    "error_message": str(e),
                })
                continue

            if broker_response.get("execution_status") == "Filled":
                reconcile_status = "FILLED"
                approval = "Approved"
            else:
                reconcile_status = "FAILED"
                approval = "Failed"

            state_manager.update_trade(request_id, broker_response, status=reconcile_status)

            if not state_manager.has_processed(request_id):
                state_manager.record_processed_result(request_id, {
                    "approval_status": approval,
                    "reason": "Reconciled after restart",
                    "execution_result": broker_response,
                })

            logger.info({"event": "reconcile_update", "request_id": request_id, "status": reconcile_status})

        logger.info({"event": "reconcile_complete"})
