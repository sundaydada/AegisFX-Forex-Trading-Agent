import os

FLAG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "trading_enabled.flag",
)


def is_trading_enabled() -> bool:
    if not os.path.exists(FLAG_FILE):
        return True
    with open(FLAG_FILE, "r") as f:
        value = f.read().strip()
    return value == "1"


def set_trading_enabled(enabled: bool) -> None:
    with open(FLAG_FILE, "w") as f:
        f.write("1" if enabled else "0")
