from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _csv_path(data_dir: Path) -> Path:
    return data_dir / "mikrotik_link.csv"


CSV_FIELDS = [
    "timestamp_utc",
    "snr_db",
    "signal_dbm",
    "tx_dbm",
    "rx_dbm",
    "noise_floor_dbm",
    "tx_rate_mbps",
    "rx_rate_mbps",
    "boat_lat",
    "boat_lon",
    "ref_lat",
    "ref_lon",
    "distance_m",
    "ap_mac",
    "wlan_iface",
]

_lock = Lock()


def append_row(data_dir: Path, row: dict[str, Any]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _csv_path(data_dir)
    write_header = not path.is_file() or path.stat().st_size == 0
    line = {k: row.get(k, "") for k in CSV_FIELDS}
    with _lock:
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if write_header:
                w.writeheader()
            w.writerow(line)


def read_history(data_dir: Path, minutes: float = 20.0) -> list[dict[str, Any]]:
    path = _csv_path(data_dir)
    if not path.is_file():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60.0
    rows: list[dict[str, Any]] = []
    with _lock:
        with path.open("r", newline="", encoding="utf-8") as f:
            r = csv.DictReader(f)
            for row in r:
                ts = row.get("timestamp_utc") or ""
                try:
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if t.timestamp() >= cutoff:
                        rows.append(row)
                except ValueError:
                    continue
    return rows


def csv_path_for_download(data_dir: Path) -> Path:
    return _csv_path(data_dir)
