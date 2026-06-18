from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import httpx

from app.csv_log import append_row
from app.geo import haversine_m, initial_bearing_deg
from app.mavlink_reader import detect_vehicle_system_id, fetch_global_position
from app.mavlink_sender import (
    detect_component_collisions,
    planned_component_ids,
    send_named_value_floats,
)
from app.mikrotik_client import (
    fetch_registration_table,
    fetch_wireless_channel,
    summarize_link,
)
from app.reachability import icmp_reachable, radio_reachable
from app.settings_store import _data_dir, load_settings

log = logging.getLogger(__name__)


@dataclass
class PollerState:
    reachable: bool = False
    reach_method: str = ""
    last_error: str | None = None
    last_link: dict[str, Any] | None = None
    last_gps: dict[str, float] | None = None
    last_distance_m: float | None = None
    last_bearing_deg: float | None = None
    vehicle_system_id: int = 1
    last_mavlink_errors: list[str] = field(default_factory=list)
    rows_logged: int = 0
    registration_path: str | None = None
    # AP (topside / base-station radio) ping result. The independent watchdog
    # also writes this; the poller publishes it as MTK_APUP heartbeat.
    ap_pingable: bool | None = None
    # Cumulative count of watchdog-triggered poller restarts in this process.
    poller_restarts: int = 0
    # monotonic() of the last watchdog-triggered restart, used by the UI for
    # the "last restart Xs ago" line and by the watchdog debounce.
    last_restart_monotonic: float | None = None
    # monotonic() of the last fetch_registration_table that hit our hard async
    # timeout; an indicator of a wedged routeros-api worker.
    last_registration_timeout_monotonic: float | None = None


_state_lock = Lock()
STATE = PollerState()
_cached_sid: int | None = None
_sid_checked_at: float = 0.0
_SID_TTL_S = 120.0

# Cached collision-check result. Probing mavlink2rest for slot occupancy on every
# poll would be wasteful, so we re-check at most every _COLLISION_TTL_S seconds
# (or immediately when the (read_base, system_id, component_id_base) key changes,
# e.g. after a settings save). Catches late-starting extensions without spamming.
_collision_key: tuple[str, int, int] | None = None
_collision_warnings: list[str] = []
_collision_checked_at: float = 0.0
_COLLISION_TTL_S = 60.0


def invalidate_vehicle_cache() -> None:
    global _cached_sid, _sid_checked_at, _collision_key, _collision_warnings, _collision_checked_at
    _cached_sid = None
    _sid_checked_at = 0.0
    _collision_key = None
    _collision_warnings = []
    _collision_checked_at = 0.0


async def _check_component_collisions(
    client: httpx.AsyncClient,
    read_base: str,
    system_id: int,
    component_id_base: int,
) -> list[str]:
    """Return cached collision warnings; refresh from mavlink2rest at most every TTL."""
    global _collision_key, _collision_warnings, _collision_checked_at
    key = (read_base, system_id, component_id_base)
    now = time.monotonic()
    if key == _collision_key and (now - _collision_checked_at) < _COLLISION_TTL_S:
        return list(_collision_warnings)
    planned = planned_component_ids(component_id_base)
    try:
        warnings = await detect_component_collisions(read_base, system_id, planned, client)
    except Exception as e:
        log.debug("Collision probe failed: %s", e)
        warnings = []
    _collision_key = key
    _collision_warnings = warnings
    _collision_checked_at = now
    return list(warnings)


def get_state() -> PollerState:
    with _state_lock:
        return PollerState(
            reachable=STATE.reachable,
            reach_method=STATE.reach_method,
            last_error=STATE.last_error,
            last_link=dict(STATE.last_link) if STATE.last_link else None,
            last_gps=dict(STATE.last_gps) if STATE.last_gps else None,
            last_distance_m=STATE.last_distance_m,
            last_bearing_deg=STATE.last_bearing_deg,
            vehicle_system_id=STATE.vehicle_system_id,
            last_mavlink_errors=list(STATE.last_mavlink_errors),
            rows_logged=STATE.rows_logged,
            registration_path=STATE.registration_path,
            ap_pingable=STATE.ap_pingable,
            poller_restarts=STATE.poller_restarts,
            last_restart_monotonic=STATE.last_restart_monotonic,
            last_registration_timeout_monotonic=STATE.last_registration_timeout_monotonic,
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
            link_summary: dict[str, Any] | None = None
            entries: list[dict[str, Any]] = []
            reg_detail = ""
            gps: dict[str, float] | None = None
            mav_read_err: str | None = None
            registration_hung = False

            # AP ping happens first so we have a value for the MTK_APUP heartbeat
            # even when everything downstream fails. The independent watchdog
            # task also overwrites STATE.ap_pingable from its own cadence; the
            # poller's write here just keeps the publish-side value fresh.
            try:
                ap_pingable = await icmp_reachable(s.ap_radio_ip)
            except Exception:
                ap_pingable = False
            _update_state(ap_pingable=ap_pingable)

            ok = False
            try:
                ok, method = await radio_reachable(s.router_ip, s.router_api_port)
                _update_state(reachable=ok, reach_method=method)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _update_state(reachable=False, last_error=str(e), registration_path=None)
                method = "error"

            if not ok:
                # Don't skip the cycle the way the old code did -- we still want
                # to publish heartbeat + DISTM/BRNG so the .BIN log proves the
                # extension is alive while the link to the boat-side radio is bad.
                _update_state(
                    last_link=None,
                    registration_path=None,
                    last_error=f"Radio unreachable ({s.router_ip}:{s.router_api_port})",
                )
                link_summary = None
                entries = []
                reg_detail = "radio unreachable"
            else:
                # Hard async ceiling around the routeros-api thread. Without this
                # the loop can park on `await to_thread(...)` for the OS TCP
                # timeout when the AP link drops mid-call -- exactly the wedge
                # that froze MTK_* at 17:06 in the reference .BIN trace.
                api_timeout = float(s.routeros_api_timeout_s)
                hard_timeout = api_timeout * 2 + 2.0
                if s.router_try_wifiwave2:
                    hard_timeout += api_timeout * 2
                try:
                    entries, reg_detail = await asyncio.wait_for(
                        asyncio.to_thread(
                            fetch_registration_table,
                            s.router_ip,
                            s.router_api_port,
                            s.router_username,
                            s.router_password,
                            plaintext_login=s.router_plaintext_login,
                            try_wifiwave2=s.router_try_wifiwave2,
                            socket_timeout_s=api_timeout,
                        ),
                        timeout=hard_timeout,
                    )
                    link_summary = summarize_link(entries)
                    # Channel isn't in the registration table; it comes from
                    # `/interface wireless monitor`. Only worth a (separate,
                    # short) API session when we actually have an associated
                    # link with a known interface. Best-effort: any failure
                    # just leaves the channel columns blank for this row.
                    if link_summary and link_summary.get("interface"):
                        try:
                            channel_info, _chan_err = await asyncio.wait_for(
                                asyncio.to_thread(
                                    fetch_wireless_channel,
                                    s.router_ip,
                                    s.router_api_port,
                                    s.router_username,
                                    s.router_password,
                                    link_summary["interface"],
                                    plaintext_login=s.router_plaintext_login,
                                    socket_timeout_s=api_timeout,
                                ),
                                timeout=api_timeout + 1.0,
                            )
                            if channel_info:
                                link_summary.update(channel_info)
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            log.debug("Channel monitor fetch failed: %s", e)
                except asyncio.TimeoutError:
                    registration_hung = True
                    entries = []
                    link_summary = None
                    reg_detail = f"fetch_registration_table hard-timeout after {hard_timeout:.1f}s"
                    log.warning("RouterOS API call exceeded hard timeout (%.1fs); skipping this cycle", hard_timeout)
                    _update_state(last_registration_timeout_monotonic=time.monotonic())
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    entries = []
                    link_summary = None
                    reg_detail = f"fetch_registration_table error: {e}"
                    log.warning("RouterOS fetch failed: %s", e, exc_info=False)

            reg_path = reg_detail if entries else None
            if not entries and reg_detail:
                log.info("RouterOS registration empty/diagnostic: %s", reg_detail)

            sid: int | None = None
            try:
                sid = await _ensure_system_id(client, s.mavlink_rest_read_base)
                gps = await fetch_global_position(
                    s.mavlink_rest_read_base,
                    sid,
                    s.gps_component_id,
                    client,
                )
            except Exception as e:
                mav_read_err = str(e)
                log.warning("MAVLink GPS read failed: %s", e, exc_info=True)

            dist: float | None = None
            brng: float | None = None
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
                    bl, gl = gps["lat"], gps["lon"]
                    dist = haversine_m(bl, gl, ref_lat, ref_lon)
                    brng = initial_bearing_deg(ref_lat, ref_lon, bl, gl)
                except Exception:
                    dist = None
                    brng = None

            status_parts: list[str] = []
            if registration_hung:
                status_parts.append(
                    f"RouterOS: API call hung; aborted via hard timeout. AP pingable={ap_pingable}."
                )
            elif not entries:
                if reg_detail:
                    status_parts.append(
                        f"RouterOS: no link data ({reg_detail}). "
                        "Check API user/password (factory default is often admin with empty password), "
                        "API service enabled, and legacy plaintext only if required."
                    )
                else:
                    status_parts.append(
                        "RouterOS: registration table empty (not associated to an AP?)."
                    )
            if not ap_pingable:
                status_parts.append(
                    f"AP {s.ap_radio_ip} not pingable; wireless link likely down."
                )
            if mav_read_err:
                status_parts.append(f"MAVLink GPS: {mav_read_err}")

            combined_error = " ".join(status_parts) if status_parts else None

            _update_state(
                last_link=link_summary,
                last_gps=gps,
                last_distance_m=dist,
                last_bearing_deg=brng,
                last_error=combined_error,
                registration_path=reg_path,
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
                "boat_alt_m": "",
                "ref_lat": ref_lat if ref_lat is not None else "",
                "ref_lon": ref_lon if ref_lon is not None else "",
                "distance_m": dist if dist is not None else "",
                "bearing_deg": brng if brng is not None else "",
                "ap_mac": (link_summary or {}).get("ap_mac") or "",
                "wlan_iface": (link_summary or {}).get("interface") or "",
                # Wireless-link state explicit in CSV so the absence of SNR/signal
                # later in the file has unambiguous context.
                "ap_pingable": 1 if ap_pingable else 0,
                "channel": (link_summary or {}).get("channel") or "",
                "frequency_mhz": (link_summary or {}).get("frequency_mhz")
                if link_summary and link_summary.get("frequency_mhz") is not None
                else "",
                "channel_width_mhz": (link_summary or {}).get("channel_width_mhz")
                if link_summary and link_summary.get("channel_width_mhz") is not None
                else "",
            }

            try:
                append_row(_data_dir(), row)
                with _state_lock:
                    STATE.rows_logged += 1
            except Exception as e:
                _update_state(last_error=f"CSV: {e}")

            # NVF dict assembled liberally so the autopilot keeps receiving
            # something every cycle even with the radio link down:
            #   * MTK_OK / MTK_APUP fire every cycle when emit_heartbeat=True.
            #   * MTK_DISTM / MTK_BRNG need only GPS + a saved reference, so they
            #     are decoupled from link_summary -- in the 17:06 trace they also
            #     stopped, even though the boat was still moving and had a GPS
            #     lock the whole time.
            #   * MTK_SNR / MTK_TXDB / MTK_RXDB still require a registration row.
            if s.mavlink_enabled:
                nvf: dict[str, float] = {}
                if s.emit_heartbeat:
                    nvf["MTK_OK"] = 1.0
                    nvf["MTK_APUP"] = 1.0 if ap_pingable else 0.0
                if link_summary:
                    if link_summary.get("snr_db") is not None:
                        nvf["MTK_SNR"] = float(link_summary["snr_db"])
                    if link_summary.get("tx_dbm") is not None:
                        nvf["MTK_TXDB"] = float(link_summary["tx_dbm"])
                    if link_summary.get("rx_dbm") is not None:
                        nvf["MTK_RXDB"] = float(link_summary["rx_dbm"])
                    if link_summary.get("frequency_mhz") is not None:
                        nvf["MTK_FREQ"] = float(link_summary["frequency_mhz"])
                if s.mavlink_send_distance and dist is not None:
                    nvf["MTK_DISTM"] = float(dist)
                if s.mavlink_send_distance and brng is not None:
                    nvf["MTK_BRNG"] = float(brng)
                if nvf:
                    collision_warnings: list[str] = []
                    if sid is not None:
                        collision_warnings = await _check_component_collisions(
                            client,
                            s.mavlink_rest_read_base,
                            sid,
                            s.mavlink_header_component_id,
                        )
                    try:
                        errs = await send_named_value_floats(
                            s.mavlink_rest_post_url,
                            client,
                            nvf,
                            s.mavlink_header_system_id,
                            s.mavlink_header_component_id,
                        )
                        _update_state(last_mavlink_errors=collision_warnings + errs)
                    except Exception as e:
                        _update_state(last_mavlink_errors=collision_warnings + [str(e)])
                else:
                    _update_state(last_mavlink_errors=[])
            else:
                _update_state(last_mavlink_errors=[])

            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass


def note_poller_restart() -> None:
    """Record that the watchdog has just (re)started the poller task."""
    with _state_lock:
        STATE.poller_restarts += 1
        STATE.last_restart_monotonic = time.monotonic()


async def ap_watchdog_loop(
    stop: asyncio.Event,
    poller_holder: dict[str, asyncio.Task[Any] | None],
) -> None:
    """Independent task that pings the topside AP and restarts the poller on
    a False -> True transition.

    Runs in its own asyncio task so a wedge inside the poller (e.g. routeros-api
    blocking inside `asyncio.to_thread`) cannot stop the watchdog from observing
    the link state. The actual ICMP work is done in `icmp_reachable`, which
    runs `icmplib.ping` on a separate `to_thread` worker -- if the poller has
    one of the default worker slots blocked, the watchdog's ping still has
    siblings available.

    Restart policy: on `prev_pingable is False and now True`, debounced by
    `watchdog_restart_debounce_s` from the last restart. We deliberately do
    NOT restart while the AP is sustained-unpingable -- if the boat is just
    out of range, restarting fixes nothing and only causes log churn.
    """
    prev_pingable: bool | None = None
    last_restart_t: float = 0.0
    # First tick: do not restart based on transition (no history yet).
    bootstrap = True
    while not stop.is_set():
        s = load_settings()
        check_interval = float(s.watchdog_check_interval_s)
        debounce = float(s.watchdog_restart_debounce_s)
        try:
            up = await icmp_reachable(s.ap_radio_ip)
        except Exception:
            up = False
        _update_state(ap_pingable=up)

        now = time.monotonic()
        if not bootstrap and prev_pingable is False and up and (now - last_restart_t) >= debounce:
            poller = poller_holder.get("task")
            if poller is not None and not poller.done():
                log.warning(
                    "AP %s came back up; restarting poller task (debounce %.1fs honoured)",
                    s.ap_radio_ip, debounce,
                )
                poller.cancel()
                # Don't block the watchdog if the old task is wedged inside an
                # uncancellable `to_thread` call (the wedged-routeros-api case
                # we're guarding against). Wait at most 2 s for it to exit;
                # if not, replace its slot and let the kernel TCP timeout
                # eventually free the orphaned worker thread.
                try:
                    await asyncio.wait_for(asyncio.shield(poller), timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                    pass
            note_poller_restart()
            last_restart_t = now
            poller_holder["task"] = asyncio.create_task(poller_loop(stop), name="poller_loop")

        prev_pingable = up
        bootstrap = False
        try:
            await asyncio.wait_for(stop.wait(), timeout=check_interval)
        except asyncio.TimeoutError:
            pass
