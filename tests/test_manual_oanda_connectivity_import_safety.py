"""Import-safety contract for the manual OANDA connectivity script.

test_oanda.py is a deliberate manual practice-account check, but its
filename matches pytest discovery, so importing it must never read the
repository .env, mutate the process environment, or reach the broker.
This test executes the script through an isolated import specification
with the .env open and the broker balance call both intercepted, so it
is deterministic and fully offline in both red and green states.
"""

import builtins
import importlib.util
import io
import os
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "test_oanda.py"

_PROBE_MODULE_NAME = "manual_oanda_connectivity_import_probe"

_PLACEHOLDER_ENV_TEXT = (
    "OANDA_DEMO_API_KEY=import-safety-placeholder\n"
    "OANDA_ACCOUNT_ID=import-safety-placeholder\n"
)


def test_manual_oanda_connectivity_script_import_is_side_effect_free(
    monkeypatch,
):
    import brokers.oanda_broker as oanda_broker_module

    environment_before = dict(os.environ)

    dotenv_reads = []
    original_open = builtins.open

    def recording_open(file, *args, **kwargs):
        if Path(str(file)).name == ".env":
            dotenv_reads.append(str(file))
            return io.StringIO(_PLACEHOLDER_ENV_TEXT)
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", recording_open)

    balance_calls = []

    def recording_get_account_balance(self):
        balance_calls.append(True)
        return 100000.0

    monkeypatch.setattr(
        oanda_broker_module.OandaBroker,
        "get_account_balance",
        recording_get_account_balance,
    )

    spec = importlib.util.spec_from_file_location(
        _PROBE_MODULE_NAME,
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PROBE_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    finally:
        environment_after_import = dict(os.environ)
        os.environ.clear()
        os.environ.update(environment_before)
        sys.modules.pop(_PROBE_MODULE_NAME, None)

    assert dotenv_reads == [], (
        ".env must not be read during import: the manual connectivity"
        " script may only load configuration inside main()"
    )
    assert balance_calls == [], (
        "the broker balance method must not run during import: no"
        " broker call may occur at collection time"
    )
    assert environment_after_import == environment_before, (
        "import must not mutate process environment variables: loading"
        " .env into os.environ belongs inside main()"
    )
    assert callable(getattr(module, "main", None)), (
        "the deliberate manual workflow must be exposed through a"
        " main() entry point guarded by __main__"
    )
