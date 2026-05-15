from __future__ import annotations

import asyncio
import contextlib
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
from app.mikrotik_client import fetch_registration_table, summarize_link
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
    # AP (topside / base-station radio) ping result. None means the loop has not
    # probed yet this session. Updated each poll cycle and published as MTK_APUP.
    ap_pingable: bool | None = None
    # monotonic() at the last *successful* NAMED_VALUE_FLOAT publish (all POSTs
    # returned 2xx). The supervisor watches this against poll_stall_restart_s.
    last_publish_monotonic: float | None = None
    # monotonic() at the start of the most recent fetch_registration_table that
    # tripped the asyncio.wait_for hard timeout (i.e., the radio API hung).
    last_registration_timeout_monotonic: float | None = None
    # Cumulative count of supervisor-triggered poller-task restarts.
    poller_restarts: int = 0


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
            last_publish_monotonic=STATE.last_publish_monotonic,
            last_registration_timeout_monotonic=STATE.last_registration_timeout_monotonic,
            poller_restarts=STATE.poller_restarts,
        )


def note_poller_restart() -> None:
    """Called by the supervisor when it cancels and restarts the poller task."""
    with _state_lock:
        STATE.poller_restarts += 1


def watchdog_should_restart(
    stall_s: float,
    require_ap_pingable: bool = True,
) -> tuple[bool, str]:
    """Watchdog predicate consulted by the supervisor.

    Returns (should_restart, reason). The supervisor restarts the poller iff:
      * we have ever published successfully (otherwise nothing to compare to),
      * the gap since the last successful publish exceeds `stall_s`, and
      * (by default) the AP is currently pingable -- when the AP is genuinely
        down there is nothing for a restart to recover, so we wait.
    """
    with _state_lock:
        last = STATE.last_publish_monotonic
        ap = STATE.ap_pingable
    if last is None:
        return False, "no successful publish yet; nothing to compare"
    gap = time.monotonic() - last
    if gap < stall_s:
        return False, f"recent publish {gap:.1f}s ago (< {stall_s:.1f}s)"
    if require_ap_pingable and ap is not True:
        return False, f"stalled {gap:.1f}s but AP not pingable (ap_pingable={ap}); link is genuinely down"
    return True, f"stalled {gap:.1f}s with AP pingable={ap}; restarting"


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


_WATCHDOG_CHECK_INTERVAL_S = 5.0


async def supervised_poller(stop: asyncio.Event) -> None:
    """Wrap poller_loop with a watchdog that restarts it when stalled.

    The supervisor never cancels the inner task while the AP is unpingable --
    if the wireless link is genuinely down there is nothing for a fresh
    poller_loop to recover, and yanking the loop would only spam the logs.
    When the AP comes back and `last_publish_monotonic` is still older than
    `poll_stall_restart_s`, we conclude that the poller (or one of its blocking
    threads) is wedged and replace it.

    Cancellation of `supervised_poller` (e.g. FastAPI shutdown) propagates to
    the inner task so the lifespan tears down cleanly.
    """
    while not stop.is_set():
        task = asyncio.create_task(poller_loop(stop), name="poller_loop")
        restart_reason: str | None = None
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=_WATCHDOG_CHECK_INTERVAL_S)
                    break  # task finished on its own
                except asyncio.TimeoutError:
                    pass
                s = load_settings()
                should, reason = watchdog_should_restart(float(s.poll_stall_restart_s))
                if should:
                    restart_reason = reason
                    break
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            raise

        if restart_reason is not None:
            log.warning("Watchdog restarting poller: %s", restart_reason)
            note_poller_restart()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            # Brief pause prevents tight restart loops if the next cycle also
            # stalls immediately; the watchdog gap of poll_stall_restart_s
            # already provides the main backoff but the extra second is cheap.
            await asyncio.sleep(1.0)
            continue

        # Inner task ended without supervisor intervention -- propagate any
        # exception for logging and either honour the stop signal or restart.
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("poller_loop exited unexpectedly: %s", e, exc_info=True)
            note_poller_restart()
        if not stop.is_set():
            await asyncio.sleep(1.0)


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

            # AP (topside / base-station radio) ping. Independent of the boat-side
            # MikroTik probe below; this is the test the watchdog uses to decide
            # whether the wireless link is alive. We do it first so we have an
            # MTK_APUP value to emit even when everything else fails.
            try:
                ap_pingable = await icmp_reachable(s.ap_radio_ip)
            except Exception:
                ap_pingable = False
            _update_state(ap_pingable=ap_pingable)

            try:
                ok, method = await radio_reachable(s.router_ip, s.router_api_port)
                _update_state(reachable=ok, reach_method=method)
                if not ok:
                    # Even when the boat-side MikroTik is unreachable, fall
                    # through to publish heartbeat / DISTM / BRNG so the .BIN
                    # log keeps proving the extension is alive.
                    _update_state(
                        last_error=f"Radio unreachable ({s.router_ip}:{s.router_api_port})",
                        last_link=None,
                        registration_path=None,
                    )
                    link_summary = None
                    reg_detail = "radio unreachable"
                    entries = []
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _update_state(reachable=False, last_error=str(e), registration_path=None)
                ok = False
                link_summary = None
                entries = []
                reg_detail = f"reachability probe error: {e}"

            # Only attempt the RouterOS API call when the boat-side radio TCP
            # probe succeeded. Wrap in asyncio.wait_for so a wedged routeros-api
            # thread can never freeze this loop -- worst case the thread leaks
            # for socket_timeout * 2 seconds and we move on with empty data.
            if ok:
                api_timeout = float(s.routeros_api_timeout_s)
                # Hard ceiling: per-call socket timeout + a small buffer for the
                # second login-mode retry + the wifiwave2 fallback if enabled.
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
                # Track wireless-link reachability in the CSV so the absence of
                # SNR/signal data later has explicit context (radio down vs poller
                # broken vs registration table empty).
                "ap_pingable": 1 if ap_pingable else 0,
            }

            try:
                append_row(_data_dir(), row)
                with _state_lock:
                    STATE.rows_logged += 1
            except Exception as e:
                _update_state(last_error=f"CSV: {e}")

            # Build the NAMED_VALUE_FLOAT dict liberally so the autopilot keeps
            # receiving something every cycle, even when the wireless link is
            # down or the registration table is empty:
            #   * MTK_OK / MTK_APUP fire every cycle (heartbeat).
            #   * MTK_DISTM / MTK_BRNG only need GPS + a saved reference, so they
            #     are decoupled from link_summary -- previously they were also
            #     suppressed on link loss, hiding the boat from .BIN logs.
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
                        if not errs:
                            _update_state(last_publish_monotonic=time.monotonic())
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
