"""
Centralized startup logging helpers.

Both the Streamlit dashboard and the dry-run loop need to log the
DB path on startup. Without a shared helper, multiple call sites
result in duplicated log lines.
"""

import os

_LOGGED_KEYS = set()


def log_db_path_once(component: str, db_path: str) -> None:
    """
    Print the resolved DB path exactly once per process, per (component, path).

    Args:
        component: Short identifier for the calling process
                   (e.g. "dashboard", "dry_run_sustained").
        db_path: The SQLite path string passed to the state manager.
    """
    key = (component, os.path.abspath(db_path))
    if key in _LOGGED_KEYS:
        return
    _LOGGED_KEYS.add(key)
    print(f"[{component}] DB PATH: {os.path.abspath(db_path)}")
