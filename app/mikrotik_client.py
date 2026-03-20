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


def _login_plaintext_modes(user_wants_plaintext: bool, password: str) -> list[bool]:
    """Order of plaintext_login flag to try with routeros-api.

    MikroTik post-6.43 docs describe a single ``/login`` with ``=name=`` and ``=password=``
    (empty password allowed). That maps to ``plaintext_login=True`` in socialwifi/routeros-api.

    The older two-step challenge (first ``/login``, then ``=response=``) uses
    ``plaintext_login=False``.

    Many RouterOS 6.x units with **no password** accept only the plaintext-style login;
    challenge with empty password may still return error 6 on some builds.
    """
    if password == "":
        return [True, False]
    if user_wants_plaintext:
        return [True, False]
    return [False, True]


def _fetch_path_with_login_fallback(
    host: str,
    port: int,
    username: str,
    password: str,
    plaintext_setting: bool,
    path: str,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return (rows, None) on success, or (None, combined_errors)."""
    errors: list[str] = []
    for pl in _login_plaintext_modes(plaintext_setting, password):
        pool: Any = None
        try:
            pool = routeros_api.RouterOsApiPool(
                host,
                username=username,
                password=password,
                port=port,
                plaintext_login=pl,
            )
            api = pool.get_api()
            reg = api.get_resource(path)
            rows = reg.get()
            try:
                pool.disconnect()
            except Exception:
                pass
            return rows, None
        except Exception as e:
            if pool is not None:
                try:
                    pool.disconnect()
                except Exception:
                    pass
            errors.append(f"plaintext_login={pl}: {e}")
    return None, " | ".join(errors)


def fetch_registration_table(
    host: str,
    port: int,
    username: str,
    password: str,
    *,
    plaintext_login: bool = False,
    try_wifiwave2: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    """Return (rows, diagnostic).

    Tries classic wireless registration table first, then wifiwave2 (RouterOS 7+ / some packages).

    Login: for **empty password**, tries **plaintext-style** ``/login`` first, then challenge-style
    (see `_login_plaintext_modes`). For non-empty password, tries your **plaintext_login** setting
    first, then the other mode.
    """
    paths: list[str] = ["/interface/wireless/registration-table"]
    if try_wifiwave2:
        paths.append("/interface/wifiwave2/registration-table")

    notes: list[str] = []
    for path in paths:
        rows, err = _fetch_path_with_login_fallback(
            host, port, username, password, plaintext_login, path
        )
        if err is not None:
            notes.append(f"{path}: {err}")
            continue
        assert rows is not None
        if rows:
            return rows, path
        notes.append(f"{path}: 0 rows")

    return [], " | ".join(notes) if notes else "no registration paths tried"


def summarize_link(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    e = entries[0]
    # wifiwave2 may use slightly different keys
    snr = e.get("signal-to-noise") or e.get("snr")
    sig = e.get("signal-strength") or e.get("signal")
    tx = e.get("tx-signal-strength") or e.get("tx-signal")
    rx = e.get("rx-signal-strength") or e.get("rx-signal")
    return {
        "ap_mac": e.get("mac-address"),
        "interface": e.get("interface"),
        "snr_db": _parse_numeric(snr),
        "signal_dbm": _parse_numeric(sig),
        "tx_dbm": _parse_numeric(tx),
        "rx_dbm": _parse_numeric(rx),
        "noise_floor_dbm": _parse_numeric(e.get("noise-floor")),
        "tx_rate_mbps": _parse_numeric(e.get("tx-rate")),
        "rx_rate_mbps": _parse_numeric(e.get("rx-rate")),
        "ccq_percent": _parse_numeric(e.get("ccq")),
        "uptime": e.get("uptime"),
    }
