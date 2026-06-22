"""
AegisFX Autonomy Supervisor

Always-on wrapper around autonomy_nightly_runner.py. Runs the runner as
a child process and:

    - auto-restarts on crash (with exponential backoff capped at 60s)
    - relays Ctrl+C cleanly to the child and waits for graceful exit
    - emits structured health logs to logs/autonomy_supervisor.log
    - never touches orchestrator, risk, broker, or queue code paths

This supervisor is a pure process manager. It contains NO trading logic.
The runner it supervises still uses AutonomyExecutionBridge ->
ProposalExecutionBridge -> TradeOrchestrator. Every existing safety
gate (idempotency, circuit breaker, rate limiter, netting, risk
evaluator, broker health, trading_enabled.flag, autonomy_settings.json)
remains in force. The supervisor cannot bypass them — it never touches
their code path.

Run:
    python autonomy_supervisor.py

Stop:
    Ctrl+C (single press) — graceful shutdown of child, then exit.
"""

import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

# ---------- Configuration ----------
RUNNER_SCRIPT = "autonomy_nightly_runner.py"
LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "autonomy_supervisor.log")
INITIAL_BACKOFF_SECONDS = 2
MAX_BACKOFF_SECONDS = 60
HEALTH_LOG_INTERVAL_SECONDS = 60
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 30

# ---------- Logger setup ----------
def _build_logger() -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    log = logging.getLogger("aegisfx.autonomy_supervisor")
    log.setLevel(logging.INFO)
    log.propagate = False

    if log.handlers:
        return log

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    # Rotating file handler — 5 MB per file, keep last 5
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5
    )
    fh.setFormatter(fmt)
    log.addHandler(fh)

    # Console mirror
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    log.addHandler(ch)

    return log


logger = _build_logger()


def _emit(event: str, **fields) -> None:
    """Structured log line — one JSON object per event for grep/parse."""
    record = {"event": event, **fields}
    logger.info(json.dumps(record, default=str))


# ---------- Process management ----------
class _ShutdownRequested(Exception):
    """Raised when the operator presses Ctrl+C."""
    pass


_shutdown_flag = {"requested": False}


def _install_signal_handlers() -> None:
    def handler(signum, _frame):
        _shutdown_flag["requested"] = True
        _emit("supervisor_signal_received", signal=signum)

    signal.signal(signal.SIGINT, handler)
    # SIGTERM exists on Unix; on Windows it maps to a no-op for non-console
    # processes, but signal.signal still accepts it.
    try:
        signal.signal(signal.SIGTERM, handler)
    except (ValueError, AttributeError):
        pass


def _spawn_runner() -> subprocess.Popen:
    """Start the runner as a child process inheriting our environment."""
    python_exe = sys.executable
    args = [python_exe, "-u", RUNNER_SCRIPT]
    # On Windows we want Ctrl+C to NOT propagate automatically to the child —
    # we want to relay it explicitly so we can time the shutdown.
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    child = subprocess.Popen(args, **kwargs)
    _emit("supervisor_runner_spawned", pid=child.pid, command=" ".join(args))
    return child


def _stop_runner(child: subprocess.Popen) -> None:
    """Send graceful shutdown signal to the runner and wait for exit."""
    if child.poll() is not None:
        return

    _emit("supervisor_graceful_stop_requested", pid=child.pid)

    try:
        if os.name == "nt":
            # CTRL_BREAK_EVENT is the only signal Popen with
            # CREATE_NEW_PROCESS_GROUP can receive on Windows.
            child.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            child.send_signal(signal.SIGINT)
    except Exception as e:
        _emit("supervisor_signal_send_failed", error=str(e))

    deadline = time.time() + GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS
    while time.time() < deadline:
        if child.poll() is not None:
            _emit("supervisor_runner_stopped_gracefully",
                  pid=child.pid, exit_code=child.returncode)
            return
        time.sleep(0.5)

    # Timeout — force terminate
    _emit("supervisor_force_terminate", pid=child.pid,
          waited_s=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS)
    try:
        child.terminate()
        child.wait(timeout=5)
    except Exception:
        try:
            child.kill()
            child.wait(timeout=5)
        except Exception as e:
            _emit("supervisor_kill_failed", pid=child.pid, error=str(e))


def _next_backoff(current: int) -> int:
    """Double the backoff up to MAX_BACKOFF_SECONDS."""
    return min(current * 2, MAX_BACKOFF_SECONDS) if current > 0 else INITIAL_BACKOFF_SECONDS


def _runner_script_exists() -> bool:
    return os.path.isfile(RUNNER_SCRIPT)


# ---------- Main supervision loop ----------
def main() -> int:
    started_at = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()

    _emit("supervisor_starting",
          runner=RUNNER_SCRIPT,
          log_file=LOG_FILE,
          backoff_initial_s=INITIAL_BACKOFF_SECONDS,
          backoff_max_s=MAX_BACKOFF_SECONDS,
          health_interval_s=HEALTH_LOG_INTERVAL_SECONDS,
          shutdown_timeout_s=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
          started_at=started_iso)

    if not _runner_script_exists():
        _emit("supervisor_runner_missing", path=RUNNER_SCRIPT)
        print(f"ERROR: {RUNNER_SCRIPT} not found in current directory.")
        return 2

    _install_signal_handlers()

    restart_count = 0
    backoff = 0
    next_health_log_at = time.time() + HEALTH_LOG_INTERVAL_SECONDS

    try:
        while not _shutdown_flag["requested"]:
            run_start = time.time()
            child = _spawn_runner()

            # Watch the child
            while True:
                if _shutdown_flag["requested"]:
                    _stop_runner(child)
                    raise _ShutdownRequested()

                rc = child.poll()
                if rc is not None:
                    # Child exited
                    run_duration_s = time.time() - run_start
                    _emit("supervisor_runner_exited",
                          pid=child.pid,
                          exit_code=rc,
                          run_duration_s=round(run_duration_s, 1),
                          restart_count=restart_count)
                    break

                # Periodic health log
                now = time.time()
                if now >= next_health_log_at:
                    _emit("supervisor_health",
                          pid=child.pid,
                          uptime_s=round(now - started_at, 1),
                          run_uptime_s=round(now - run_start, 1),
                          restart_count=restart_count,
                          status="running")
                    next_health_log_at = now + HEALTH_LOG_INTERVAL_SECONDS

                time.sleep(1)

            if _shutdown_flag["requested"]:
                break

            # If child ran for a long time, reset backoff; otherwise grow it
            run_duration_s = time.time() - run_start
            if run_duration_s > 5 * MAX_BACKOFF_SECONDS:
                backoff = 0
            backoff = _next_backoff(backoff)
            restart_count += 1

            _emit("supervisor_restarting",
                  backoff_s=backoff,
                  restart_count=restart_count,
                  last_run_duration_s=round(run_duration_s, 1))

            # Sleep with shutdown responsiveness
            sleep_until = time.time() + backoff
            while time.time() < sleep_until:
                if _shutdown_flag["requested"]:
                    break
                time.sleep(0.5)

    except _ShutdownRequested:
        pass

    finally:
        total_uptime_s = time.time() - started_at
        _emit("supervisor_stopped",
              total_uptime_s=round(total_uptime_s, 1),
              total_restarts=restart_count,
              stopped_at=datetime.now(timezone.utc).isoformat())

    return 0


if __name__ == "__main__":
    sys.exit(main())
