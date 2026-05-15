from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.csv_log import csv_path_for_download, read_history
from app.poller import (
    ap_watchdog_loop,
    get_state,
    invalidate_vehicle_cache,
    poller_loop,
)
from app.settings_store import ensure_data_dir, load_settings, save_settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The watchdog needs to be able to swap the live poller task out from
    # under us, so we keep the reference in a dict it can mutate. The
    # watchdog cancels & recreates `holder["task"]` on AP down -> up
    # transitions (see `ap_watchdog_loop`).
    stop = asyncio.Event()
    holder: dict[str, asyncio.Task[Any] | None] = {
        "task": asyncio.create_task(poller_loop(stop), name="poller_loop")
    }
    watchdog = asyncio.create_task(ap_watchdog_loop(stop, holder), name="ap_watchdog_loop")
    try:
        yield
    finally:
        stop.set()
        for t in (watchdog, holder.get("task")):
            if t is not None:
                t.cancel()
        for t in (watchdog, holder.get("task")):
            if t is None:
                continue
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass


app = FastAPI(title="Mikrotik Link Monitor", lifespan=lifespan)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return PlainTextResponse("UI missing; add static/index.html", status_code=500)
    return FileResponse(index)


@app.get("/register_service")
async def register_service():
    payload = {
        "name": "Mikrotik Link Monitor",
        "description": "RouterOS wireless link metrics, GPS distance and bearing to the boat, CSV logging, and MAVLink NamedValueFloat for ArduPilot logs.",
        "icon": "mdi-router-wireless",
        "company": "BlueBoat / Community",
        "version": "1.3.0",
        "webpage": "https://github.com/vshie/Mikrotik-Monitor",
        "api": "https://github.com/vshie/Mikrotik-Monitor",
        "works_in_relative_paths": True,
    }
    return JSONResponse(payload)


@app.get("/api/status")
async def api_status():
    import time

    st = get_state()
    cfg = load_settings()
    now = time.monotonic()
    secs_since_restart = (
        now - st.last_restart_monotonic if st.last_restart_monotonic is not None else None
    )
    secs_since_reg_timeout = (
        now - st.last_registration_timeout_monotonic
        if st.last_registration_timeout_monotonic is not None
        else None
    )
    return {
        "reachable": st.reachable,
        "reach_method": st.reach_method,
        "last_error": st.last_error,
        "last_link": st.last_link,
        "last_gps": st.last_gps,
        "last_distance_m": st.last_distance_m,
        "last_bearing_deg": st.last_bearing_deg,
        "vehicle_system_id": st.vehicle_system_id,
        "last_mavlink_errors": st.last_mavlink_errors,
        "rows_logged": st.rows_logged,
        "mavlink_enabled": cfg.mavlink_enabled,
        "registration_path": st.registration_path,
        "reference_latitude": cfg.reference_latitude,
        "reference_longitude": cfg.reference_longitude,
        # Watchdog fields
        "ap_radio_ip": cfg.ap_radio_ip,
        "ap_pingable": st.ap_pingable,
        "poller_restarts": st.poller_restarts,
        "seconds_since_last_restart": secs_since_restart,
        "seconds_since_last_registration_timeout": secs_since_reg_timeout,
    }


@app.get("/api/settings")
async def api_get_settings():
    ensure_data_dir()
    return load_settings().model_dump()


@app.put("/api/settings")
async def api_put_settings(body: dict[str, Any] = Body(...)):
    cur = load_settings()
    prev_read = cur.mavlink_rest_read_base
    try:
        merged = cur.merge(body)
        save_settings(merged)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if merged.mavlink_rest_read_base != prev_read:
        invalidate_vehicle_cache()
    return merged.model_dump()


@app.get("/api/history")
async def api_history(minutes: float = 20.0):
    rows = read_history(ensure_data_dir(), minutes=minutes)
    return {"minutes": minutes, "points": rows}


@app.get("/api/download/csv")
async def api_download_csv():
    path = csv_path_for_download(ensure_data_dir())
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No CSV yet")
    return FileResponse(path, filename="mikrotik_link.csv", media_type="text/csv")
