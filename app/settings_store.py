from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


class AppSettings(BaseModel):
    """Defaults: BlueBoat MikroTik ROS 6.x client (admin / no password, classic wireless)."""

    router_ip: str = "192.168.2.4"
    router_api_port: int = 8728
    router_username: str = "admin"
    router_password: str = ""
    router_plaintext_login: bool = False
    router_try_wifiwave2: bool = False
    poll_interval_s: float = Field(default=1.0, ge=0.2, le=60.0)

    # Topside / base-station AP IP. An independent watchdog task ICMP-pings
    # this address and restarts the poller task on a False -> True transition
    # (link regained). Also published as MTK_APUP heartbeat NVF (1.0 / 0.0).
    ap_radio_ip: str = "192.168.2.3"

    # Socket-level timeout on the routeros-api API session. Without this the
    # library waits the OS TCP timeout when a peer goes half-open, which is
    # exactly the failure that wedged the loop at 17:06 in the reference log
    # (.BIN trace: all five MTK_* names stop within 40 ms of each other and
    # never recover). With a 3 s cap the loop self-heals once the link is back.
    routeros_api_timeout_s: float = Field(default=3.0, ge=0.5, le=30.0)

    # AP watchdog cadence and debounce. The watchdog pings every check_interval
    # and triggers a poller restart on transition; the debounce keeps a flapping
    # link from causing back-to-back restarts.
    watchdog_check_interval_s: float = Field(default=5.0, ge=1.0, le=60.0)
    watchdog_restart_debounce_s: float = Field(default=10.0, ge=0.0, le=300.0)

    # Always emit MTK_OK = 1.0 (alive) and MTK_APUP = 1.0/0.0 every poll cycle
    # so the autopilot .BIN log records the extension and link state explicitly
    # -- prevents the "absence of data could mean anything" ambiguity we hit in
    # the 17:06 trace.
    emit_heartbeat: bool = True

    mavlink_rest_read_base: str = "http://host.docker.internal/mavlink2rest/mavlink"
    mavlink_rest_post_url: str = "http://host.docker.internal:6040/v1/mavlink"
    mavlink_enabled: bool = True
    mavlink_send_distance: bool = True
    target_system: int = 1
    target_component: int = 1
    mavlink_header_system_id: int = 255
    # Base component_id for our NAMED_VALUE_FLOAT senders. Each metric occupies
    # base + NAMED_VALUE_OFFSETS[name] (see app/mavlink_sender.py); shift this if
    # another extension's NAMED_VALUE_FLOAT range overlaps. Default 60 keeps us
    # clear of the BlueOS PH/TEMP/SALINITY/CONDUCT extension at 25-28.
    mavlink_header_component_id: int = 60

    reference_latitude: float | None = None
    reference_longitude: float | None = None

    gps_component_id: int = 1

    def merge(self, patch: dict[str, Any]) -> "AppSettings":
        data = self.model_dump()
        for k, v in patch.items():
            if k in data:
                data[k] = v
        return AppSettings.model_validate(data)


_lock = Lock()
_settings_path = lambda: _data_dir() / "settings.json"


def ensure_data_dir() -> Path:
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_settings() -> AppSettings:
    path = _settings_path()
    if not path.is_file():
        return AppSettings()
    with _lock:
        raw = path.read_text(encoding="utf-8")
    data = json.loads(raw) if raw.strip() else {}
    return AppSettings.model_validate(data)


def save_settings(settings: AppSettings) -> None:
    ensure_data_dir()
    path = _settings_path()
    tmp = path.with_suffix(".json.tmp")
    body = settings.model_dump_json(indent=2)
    with _lock:
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(path)
