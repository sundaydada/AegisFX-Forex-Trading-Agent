import logging

logger = logging.getLogger("aegisfx.broker_health")


class BrokerHealthMonitor:
    """
    System-wide broker connectivity state.
    Shared across orchestrator, dashboard, and all broker consumers.
    """

    def __init__(self):
        self._connected = True
        self._last_error = None

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str:
        return self._last_error

    def report_success(self) -> None:
        if not self._connected:
            logger.info({"event": "broker_reconnected"})
        self._connected = True
        self._last_error = None

    def report_failure(self, error: str) -> None:
        self._connected = False
        self._last_error = error
        logger.error({"event": "broker_disconnected", "error": error})
