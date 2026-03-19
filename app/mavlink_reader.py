from __future__ import annotations

from typing import Any

import httpx


async def detect_vehicle_system_id(
    read_base: str,
    client: httpx.AsyncClient,
    max_id: int = 100,
) -> int:
    """Mirror pingSurvey: list /vehicles + /info, else probe GLOBAL_POSITION_INT."""
    base = read_base.rstrip("/")
    try:
        r = await client.get(f"{base}/vehicles", timeout=2.0)
        if r.status_code == 200:
            vehicles = r.json()
            if isinstance(vehicles, dict):
                vehicles = vehicles.get("vehicles") or vehicles.get("data") or []
            if isinstance(vehicles, list) and vehicles:
                for vid in vehicles:
                    if isinstance(vid, dict):
                        vid = vid.get("id") or vid.get("vehicle_id") or vid.get("system_id")
                    if vid is None:
                        continue
                    try:
                        vid = int(vid)
                    except (TypeError, ValueError):
                        continue
                    try:
                        ir = await client.get(f"{base}/vehicles/{vid}/info", timeout=1.0)
                        if ir.status_code != 200:
                            continue
                        info = ir.json()
                        ap = (
                            (info.get("autopilot") or {}).get("type")
                            if isinstance(info, dict)
                            else None
                        )
                        valid = {
                            "MAV_AUTOPILOT_GENERIC",
                            "MAV_AUTOPILOT_ARDUPILOTMEGA",
                            "MAV_AUTOPILOT_PX4",
                        }
                        if ap in valid:
                            return int(vid)
                    except Exception:
                        continue
    except Exception:
        pass

    for candidate in range(1, max_id + 1):
        url = f"{base}/vehicles/{candidate}/components/1/messages/GLOBAL_POSITION_INT"
        try:
            gr = await client.get(url, timeout=0.75)
            if gr.status_code != 200:
                continue
            payload = gr.json()
            if isinstance(payload, dict) and "message" in payload:
                return candidate
        except Exception:
            continue
    return 1


async def fetch_global_position(
    read_base: str,
    system_id: int,
    component_id: int,
    client: httpx.AsyncClient,
) -> dict[str, float] | None:
    base = read_base.rstrip("/")
    url = f"{base}/vehicles/{system_id}/components/{component_id}/messages/GLOBAL_POSITION_INT"
    try:
        r = await client.get(url, timeout=2.0)
        if r.status_code != 200:
            return None
        payload = r.json()
        if not isinstance(payload, dict):
            return None
        msg = payload.get("message")
        if not isinstance(msg, dict):
            return None
        lat_i = msg.get("lat")
        lon_i = msg.get("lon")
        alt_i = msg.get("alt")
        if lat_i is None or lon_i is None:
            return None
        out: dict[str, float] = {
            "lat": float(lat_i) / 1e7,
            "lon": float(lon_i) / 1e7,
        }
        if alt_i is not None:
            out["alt_m"] = float(alt_i) / 1e3
        return out
    except Exception:
        return None
