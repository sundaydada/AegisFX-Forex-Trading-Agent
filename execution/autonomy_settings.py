"""
Autonomy Settings Store — Phase 1 of AegisFX autonomy build.

JSON-backed configuration for autonomous trading parameters.
This module is STORAGE ONLY — nothing in this file reads or modifies
the orchestrator, broker, or dashboard. Settings are loaded by other
components when (and if) they choose to opt into autonomy.
"""

import json
import os
from typing import Any, Dict


DEFAULT_SETTINGS: Dict[str, Any] = {
    "auto_trade_enabled": False,
    "min_confidence": 85,
    "max_trades_per_night": 10,
    "max_position_size": 1.0,
    "allowed_pairs": ["EUR/USD", "GBP/USD", "USD/JPY"],
    "allowed_risk_modes": ["NORMAL", "REDUCED"],
}

VALID_RISK_MODES = {"NORMAL", "REDUCED", "AVOID"}


class AutonomySettingsManager:
    """
    Persistent JSON-backed autonomy settings.
    Validates every read and write against schema constraints.
    """

    def __init__(self, settings_path: str = "autonomy_settings.json"):
        self._path = settings_path
        # Ensure file exists with defaults on first construction
        if not os.path.exists(self._path):
            self._write_raw(DEFAULT_SETTINGS)

    # ---------- Public API ----------

    def load_settings(self) -> Dict[str, Any]:
        """
        Load and validate the current settings.
        Returns a fresh dict (defensive copy). On any read or validation
        failure, falls back to defaults without modifying the file.
        """
        if not os.path.exists(self._path):
            self._write_raw(DEFAULT_SETTINGS)
            return dict(DEFAULT_SETTINGS)

        try:
            with open(self._path, "r") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return dict(DEFAULT_SETTINGS)

        # Merge with defaults so missing keys get filled in
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})

        try:
            self._validate(merged)
        except ValueError:
            return dict(DEFAULT_SETTINGS)

        return merged

    def save_settings(self, settings: Dict[str, Any]) -> None:
        """
        Validate and persist a full settings dict.
        Raises ValueError on validation failure (file unchanged).
        """
        if not isinstance(settings, dict):
            raise ValueError("settings must be a dict")

        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})

        self._validate(merged)
        self._write_raw(merged)

    def get_setting(self, key: str) -> Any:
        """Return a single setting value (or default if missing)."""
        return self.load_settings().get(key, DEFAULT_SETTINGS.get(key))

    def update_setting(self, key: str, value: Any) -> None:
        """
        Update a single setting and persist.
        Raises ValueError if the key is unknown or value fails validation.
        """
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"Unknown setting: {key}")

        current = self.load_settings()
        current[key] = value
        self.save_settings(current)

    def reset_defaults(self) -> None:
        """Overwrite settings file with the default values."""
        self._write_raw(DEFAULT_SETTINGS)

    # ---------- Internal helpers ----------

    def _write_raw(self, settings: Dict[str, Any]) -> None:
        with open(self._path, "w") as f:
            json.dump(settings, f, indent=2, sort_keys=True)

    @staticmethod
    def _validate(settings: Dict[str, Any]) -> None:
        """Raise ValueError if any field violates its constraint."""

        # auto_trade_enabled
        if not isinstance(settings.get("auto_trade_enabled"), bool):
            raise ValueError("auto_trade_enabled must be a bool")

        # min_confidence: integer or float in [0, 100]
        conf = settings.get("min_confidence")
        if not isinstance(conf, (int, float)) or isinstance(conf, bool):
            raise ValueError("min_confidence must be a number")
        if not (0 <= conf <= 100):
            raise ValueError("min_confidence must be between 0 and 100")

        # max_trades_per_night: integer >= 0
        max_trades = settings.get("max_trades_per_night")
        if not isinstance(max_trades, int) or isinstance(max_trades, bool):
            raise ValueError("max_trades_per_night must be an integer")
        if max_trades < 0:
            raise ValueError("max_trades_per_night must be >= 0")

        # max_position_size: number > 0
        max_pos = settings.get("max_position_size")
        if not isinstance(max_pos, (int, float)) or isinstance(max_pos, bool):
            raise ValueError("max_position_size must be a number")
        if max_pos <= 0:
            raise ValueError("max_position_size must be > 0")

        # allowed_pairs: list of strings
        pairs = settings.get("allowed_pairs")
        if not isinstance(pairs, list):
            raise ValueError("allowed_pairs must be a list")
        for p in pairs:
            if not isinstance(p, str):
                raise ValueError("allowed_pairs entries must be strings")

        # allowed_risk_modes: list, each entry from VALID_RISK_MODES
        risk_modes = settings.get("allowed_risk_modes")
        if not isinstance(risk_modes, list):
            raise ValueError("allowed_risk_modes must be a list")
        for r in risk_modes:
            if r not in VALID_RISK_MODES:
                raise ValueError(
                    f"allowed_risk_modes entries must be one of {sorted(VALID_RISK_MODES)}"
                )
