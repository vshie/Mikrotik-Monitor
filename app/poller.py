from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import httpx

from app.csv_log import append_row
from app.geo import haversine_m
from app.mavlink_reader import detect_vehicle_system_id, fetch_global_position
from app.mavlink_sender import send_named_value_floats
from app.mikrotik_client import fetch_registration_table, summarize_link
from app.reachability import radio_reachable
from app.settings_store import _data_dir, load_settings


@dataclass
class PollerState:
    reachable: bool = False
    reach_method: str = ""
    last_error: str | None = None
    last_link: dict[str, Any] | None = None
    last_gps: dict[str, float] | None = None
    last_distance_m: float | None = None
    vehicle_system_id: int = 1
    last_mavlink_errors: list[str] = field(default_factory=list)
    rows_logged: int = 0


_state_lock = Lock()
STATE = PollerState()
_cached_sid: int | None = None
_sid_checked_at: float = 0.0
_SID_TTL_S = 120.0


def invalidate_vehicle_cache() -> None:
    global _cached_sid, _sid_checked_at
    _cached_sid = None
    _sid_checked_at = 0.0


def get_state() -> PollerState:
    with _state_lock:
        return PollerState(
            reachable=STATE.reachable,
            reach_method=STATE.reach_method,
            last_error=STATE.last_error,
            last_link=dict(STATE.last_link) if STATE.last_link else None,
            last_gps=dict(STATE.last_gps) if STATE.last_gps else None,
            last_distance_m=STATE.last_distance_m,
            vehicle_system_id=STATE.vehicle_system_id,
            last_mavlink_errors=list(STATE.last_mavlink_errors),
            rows_logged=STATE.rows_logged,
        )


def _update_state(**kwargs: Any) -> None:
    global STATE
    with _state_lock:
        for k, v in kwargs.items():
            setattr(STATE, k, v)


async def _ensure_system_id(client: httpx.AsyncClient, read_base: str) -> int:
    global _cached_sid, _sid_checked_at
    now = time.monotonic()
    if _cached_sid is not None and (now - _sid_checked_at) < _SID_TTL_S:
        return _cached_sid
    sid = await detect_vehicle_system_id(read_base, client)
    _cached_sid = sid
    _sid_checked_at = now
    _update_state(vehicle_system_id=sid)
    return sid


async def poller_loop(stop: asyncio.Event) -> None:
    async with httpx.AsyncClient() as client:
        while not stop.is_set():
            s = load_settings()
            interval = float(s.poll_interval_s)
            try:
                ok, method = await radio_reachable(s.router_ip, s.router_api_port)
                _update_state(reachable=ok, reach_method=method, last_error=None)
                if not ok:
                    _update_state(
                        last_error=f"Radio unreachable ({s.router_ip}:{s.router_api_port})"
                    )
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _update_state(reachable=False, last_error=str(e))

            link_summary: dict[str, Any] | None = None
            try:
                entries = await asyncio.to_thread(
                    fetch_registration_table,
                    s.router_ip,
                    s.router_api_port,
                    s.router_username,
                    s.router_password,
                )
                link_summary = summarize_link(entries)
            except Exception as e:
                _update_state(last_error=f"RouterOS API: {e}")

            gps: dict[str, float] | None = None
            try:
                sid = await _ensure_system_id(client, s.mavlink_rest_read_base)
                gps = await fetch_global_position(
                    s.mavlink_rest_read_base,
                    sid,
                    s.gps_component_id,
                    client,
                )
            except Exception as e:
                _update_state(last_error=f"MAVLink read: {e}")

            dist: float | None = None
            ref_lat = s.reference_latitude
            ref_lon = s.reference_longitude
            if (
                gps
                and ref_lat is not None
                and ref_lon is not None
                and -90 <= ref_lat <= 90
                and -180 <= ref_lon <= 180
            ):
                try:
                    dist = haversine_m(gps["lat"], gps["lon"], ref_lat, ref_lon)
                except Exception:
                    dist = None

            _update_state(
                last_link=link_summary,
                last_gps=gps,
                last_distance_m=dist,
                last_mavlink_errors=[],
            )

            ts = datetime.now(timezone.utc).isoformat()
            row: dict[str, Any] = {
                "timestamp_utc": ts,
                "snr_db": link_summary.get("snr_db") if link_summary else "",
                "signal_dbm": link_summary.get("signal_dbm") if link_summary else "",
                "tx_dbm": link_summary.get("tx_dbm") if link_summary else "",
                "rx_dbm": link_summary.get("rx_dbm") if link_summary else "",
                "noise_floor_dbm": link_summary.get("noise_floor_dbm") if link_summary else "",
                "tx_rate_mbps": link_summary.get("tx_rate_mbps") if link_summary else "",
                "rx_rate_mbps": link_summary.get("rx_rate_mbps") if link_summary else "",
                "boat_lat": gps["lat"] if gps else "",
                "boat_lon": gps["lon"] if gps else "",
                "ref_lat": ref_lat if ref_lat is not None else "",
                "ref_lon": ref_lon if ref_lon is not None else "",
                "distance_m": dist if dist is not None else "",
                "ap_mac": (link_summary or {}).get("ap_mac") or "",
                "wlan_iface": (link_summary or {}).get("interface") or "",
            }

            try:
                append_row(_data_dir(), row)
                with _state_lock:
                    STATE.rows_logged += 1
            except Exception as e:
                _update_state(last_error=f"CSV: {e}")

            if s.mavlink_enabled and link_summary:
                nvf: dict[str, float] = {}
                if link_summary.get("snr_db") is not None:
                    nvf["MTK_SNR"] = float(link_summary["snr_db"])
                if link_summary.get("tx_dbm") is not None:
                    nvf["MTK_TXDB"] = float(link_summary["tx_dbm"])
                if link_summary.get("rx_dbm") is not None:
                    nvf["MTK_RXDB"] = float(link_summary["rx_dbm"])
                if s.mavlink_send_distance and dist is not None:
                    nvf["MTK_DISTM"] = float(dist)
                if nvf:
                    try:
                        errs = await send_named_value_floats(
                            s.mavlink_rest_post_url,
                            client,
                            nvf,
                            s.mavlink_header_system_id,
                            s.mavlink_header_component_id,
                        )
                        _update_state(last_mavlink_errors=errs)
                    except Exception as e:
                        _update_state(last_mavlink_errors=[str(e)])

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
