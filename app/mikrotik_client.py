from __future__ import annotations

import re
from typing import Any

import routeros_api


def _parse_numeric(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.upper() == "N/A":
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def fetch_registration_table(
    host: str,
    port: int,
    username: str,
    password: str,
) -> list[dict[str, Any]]:
    pool = routeros_api.RouterOsApiPool(
        host,
        username=username,
        password=password,
        port=port,
        plaintext_login=True,
    )
    api = pool.get_api()
    reg = api.get_resource("/interface/wireless/registration-table")
    rows = reg.get()
    pool.disconnect()
    return rows


def summarize_link(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    e = entries[0]
    return {
        "ap_mac": e.get("mac-address"),
        "interface": e.get("interface"),
        "snr_db": _parse_numeric(e.get("signal-to-noise")),
        "signal_dbm": _parse_numeric(e.get("signal-strength")),
        "tx_dbm": _parse_numeric(e.get("tx-signal-strength")),
        "rx_dbm": _parse_numeric(e.get("rx-signal-strength")),
        "noise_floor_dbm": _parse_numeric(e.get("noise-floor")),
        "tx_rate_mbps": _parse_numeric(e.get("tx-rate")),
        "rx_rate_mbps": _parse_numeric(e.get("rx-rate")),
        "ccq_percent": _parse_numeric(e.get("ccq")),
        "uptime": e.get("uptime"),
    }
