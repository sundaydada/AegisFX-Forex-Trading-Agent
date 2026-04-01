from typing import Dict


def display_metrics(metrics: Dict) -> None:
    total = metrics.get("total_trades", 0)
    success = metrics.get("successful_trades", 0)
    failed = metrics.get("failed_trades", 0)
    timeouts = metrics.get("timeout_trades", 0)
    exceptions = metrics.get("exception_trades", 0)

    failure_rate = (failed / total * 100) if total > 0 else 0.0

    print("=== AegisFX Monitor ===")
    print(f"Total: {total} | Success: {success} | Failed: {failed}")
    print(f"Failure Rate: {failure_rate:.1f}%")
    print(f"Timeouts: {timeouts} | Exceptions: {exceptions}")
