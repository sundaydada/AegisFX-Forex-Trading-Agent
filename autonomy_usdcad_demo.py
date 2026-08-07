"""Run exactly one supervised USD/CAD autonomous cycle.

Loads .env, reads the two OANDA demo credentials, builds the existing
practice-only dependency bundle, checks readiness, and runs the existing
cycle once. It adds no trading logic, no risk logic, no loop, and no
scheduler; every decision belongs to run_cycle and the layers beneath it.

The endpoint is not configurable here: build_dependencies hardcodes the
OANDA practice URL and accepts no base_url, so this script cannot reach
a live account. Credential values are never printed.
"""

import os

from dotenv import load_dotenv

from autonomy_usdcad_cycle import run_cycle
from autonomy_usdcad_wiring import build_dependencies, check_readiness


def main():
    load_dotenv()

    api_key = os.getenv("OANDA_DEMO_API_KEY", "").strip()
    account_id = os.getenv("OANDA_ACCOUNT_ID", "").strip()

    if not api_key or not account_id:
        print("DEMO_BLOCKED_MISSING_CREDENTIALS")
        return

    dependencies = build_dependencies(
        api_key=api_key,
        account_id=account_id,
    )

    readiness = check_readiness(dependencies)
    if readiness.get("ready") is not True:
        print("DEMO_BLOCKED_READINESS")
        print(readiness)
        return

    result = run_cycle(
        broker=dependencies["broker"],
        state_manager=dependencies["state_manager"],
        signal_provider=dependencies["signal_provider"],
        proposal_queue=dependencies["proposal_queue"],
        executor=dependencies["executor"],
    )

    print(result)


if __name__ == "__main__":
    main()
