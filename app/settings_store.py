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
    router_ip: str = "192.168.2.4"
    router_api_port: int = 8728
    router_username: str = "admin"
    # Factory default is often user "admin" with no password (empty string).
    router_password: str = ""
    # RouterOS 6.43+ uses challenge login; plaintext is for older ROS only.
    router_plaintext_login: bool = False
    router_try_wifiwave2: bool = True
    poll_interval_s: float = Field(default=1.0, ge=0.2, le=60.0)

    mavlink_rest_read_base: str = "http://host.docker.internal/mavlink2rest/mavlink"
    mavlink_rest_post_url: str = "http://host.docker.internal:6040/v1/mavlink"
    mavlink_enabled: bool = True
    mavlink_send_distance: bool = True
    target_system: int = 1
    target_component: int = 1
    mavlink_header_system_id: int = 255
    mavlink_header_component_id: int = 240

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
