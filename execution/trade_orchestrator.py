import logging
from typing import Dict, List
from datetime import datetime, timezone
from brokers.broker_interface import BrokerInterface
from execution.trade_state_manager import TradeStateManager
from execution.portfolio_risk_evaluator import PortfolioRiskEvaluator
from execution.position_netting import net_position

PENDING_TIMEOUT_SECONDS = 60

logger = logging.getLogger("aegisfx.orchestrator")


class TradeOrchestrator:
    """
    Enforces approval -> execution -> state persistence flow.
    This is the ONLY valid trade execution entry point.
    """

    def __init__(self, broker: BrokerInterface):
        self._broker = broker
        self._trade_timestamps = []
        self._max_trades_per_minute = 5
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

        # Step 5: Execute Trade via broker
        order = {
            "currency_pair": proposed_trade["currency_pair"],
            "direction": proposed_trade["direction"],
            "position_size": proposed_trade["approved_position_size"],
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
