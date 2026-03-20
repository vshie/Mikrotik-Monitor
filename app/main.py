from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.csv_log import csv_path_for_download, read_history
from app.poller import get_state, invalidate_vehicle_cache, poller_loop
from app.settings_store import ensure_data_dir, load_settings, save_settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = asyncio.Event()
    task = asyncio.create_task(poller_loop(stop))
    yield
    stop.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
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
        "version": "1.2.0",
        "webpage": "https://github.com/vshie/Mikrotik-Monitor",
        "api": "https://github.com/vshie/Mikrotik-Monitor",
        "works_in_relative_paths": True,
    }
    return JSONResponse(payload)


@app.get("/api/status")
async def api_status():
    st = get_state()
    cfg = load_settings()
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
