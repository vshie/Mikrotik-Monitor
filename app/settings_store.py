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

    # Topside (base-station) AP IP. We ping this every cycle and publish the
    # result as MTK_APUP (1.0 / 0.0); it is also the gating signal for the
    # watchdog: when no NAMED_VALUE_FLOAT has been published successfully for
    # `poll_stall_restart_s` *and* the AP is reachable, the supervisor restarts
    # the poller task. If the AP is unpingable we deliberately do NOT restart
    # (there is nothing for the poller to recover -- the link is genuinely down).
    ap_radio_ip: str = "192.168.2.3"

    # Socket-level timeout (seconds) on the routeros-api API session. Without
    # this the library can hang for the full kernel TCP timeout (~2h) if the
    # boat-side MikroTik becomes unresponsive after the TCP handshake.
    routeros_api_timeout_s: float = Field(default=3.0, ge=0.5, le=30.0)

    # Watchdog: max seconds with no successful NAMED_VALUE_FLOAT publish before
    # the supervisor restarts the poller task (only when the AP is reachable).
    poll_stall_restart_s: float = Field(default=30.0, ge=5.0, le=600.0)

    # Always emit MTK_OK and MTK_APUP heartbeat NVFs so the autopilot's .BIN
    # log unambiguously records the extension being alive and the wireless
    # link state, even when there are no link statistics to report.
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
