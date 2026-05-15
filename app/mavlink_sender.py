from __future__ import annotations

from typing import Any

import httpx

# mavlink-server's in-memory store is shaped:
#   vehicles[system_id] -> components[component_id] -> messages[message_type] -> latest
# Every NAMED_VALUE_FLOAT we POST from the same (system_id, component_id) pair lands
# in the same slot, so the last write wins and only one metric survives for the
# inspector / .BIN log. Each metric therefore gets its own component_id, computed
# as `component_id_base + NAMED_VALUE_OFFSETS[name]`. The base is configurable so
# the operator can shift the whole window if another extension claims overlapping
# IDs (see detect_component_collisions below). The default base of 60 sits well
# above the standard MAVLink component range and clear of the 25-28 range used by
# the BlueOS PH/TEMP/SALINITY/CONDUCT extension.
NAMED_VALUE_OFFSETS: dict[str, int] = {
    "MTK_SNR": 0,
    "MTK_TXDB": 1,
    "MTK_RXDB": 2,
    "MTK_DISTM": 3,
    "MTK_BRNG": 4,
    # Heartbeat values: emitted every poll cycle so the .BIN log unambiguously
    # records the extension being alive (MTK_OK) and the wireless-link state
    # (MTK_APUP). In the 17:06 reference trace, MTK_SNR vanished and there was
    # no way from the log alone to distinguish "extension crashed" from "link
    # down" -- these two NVFs make that distinction explicit.
    "MTK_OK": 5,    # always 1.0 while the poller is running
    "MTK_APUP": 6,  # 1.0 if the AP at ap_radio_ip is pingable, 0.0 otherwise
}


def planned_component_ids(component_id_base: int) -> dict[str, int]:
    """Return name -> component_id for every NAMED_VALUE_FLOAT this extension emits."""
    return {name: component_id_base + offset for name, offset in NAMED_VALUE_OFFSETS.items()}


def _nvf_name_field(name: str) -> list[str]:
    """10 single-char strings, null-padded (same shape as mavlink2rest / PME_microDOT)."""
    out: list[str] = []
    for i in range(10):
        out.append(name[i] if i < len(name) else "\x00")
    return out


def _decode_nvf_name(payload: Any) -> str | None:
    """Reverse of _nvf_name_field for a mavlink2rest GET response body."""
    if not isinstance(payload, dict):
        return None
    msg = payload.get("message")
    if not isinstance(msg, dict):
        return None
    name_field = msg.get("name")
    if isinstance(name_field, list):
        chars = [c for c in name_field if isinstance(c, str) and c and c != "\x00"]
        decoded = "".join(chars).rstrip("\x00").strip()
        return decoded or None
    if isinstance(name_field, str):
        decoded = name_field.rstrip("\x00").strip()
        return decoded or None
    return None


def _nvf_payload(
    name: str,
    value: float,
    header_system_id: int,
    header_component_id: int,
) -> dict[str, Any]:
    return {
        "header": {
            "system_id": header_system_id,
            "component_id": header_component_id,
            "sequence": 0,
        },
        "message": {
            "type": "NAMED_VALUE_FLOAT",
            "time_boot_ms": 0,
            "value": float(value),
            "name": _nvf_name_field(name),
        },
    }


async def detect_component_collisions(
    read_base: str,
    system_id: int,
    planned: dict[str, int],
    client: httpx.AsyncClient,
) -> list[str]:
    """Probe mavlink2rest for NAMED_VALUE_FLOAT slots that another sender already owns.

    For each (name, component_id) we plan to use, GET the slot and check whether
    its current occupant has a *different* name. Any collision is returned as a
    human-readable warning string. 404 / network errors are treated as "free".
    """
    base = read_base.rstrip("/")
    warnings: list[str] = []
    for name, cid in planned.items():
        url = f"{base}/vehicles/{system_id}/components/{cid}/messages/NAMED_VALUE_FLOAT"
        try:
            r = await client.get(url, timeout=1.5)
        except Exception:
            continue
        if r.status_code != 200:
            continue
        try:
            existing = _decode_nvf_name(r.json())
        except Exception:
            continue
        if existing and existing != name:
            warnings.append(
                f"component_id {cid} already holds NAMED_VALUE_FLOAT '{existing}' "
                f"(we want '{name}'). Shift the Component ID base in Settings to free this slot."
            )
    return warnings


async def send_named_value_floats(
    post_url: str,
    client: httpx.AsyncClient,
    values: dict[str, float],
    header_system_id: int,
    component_id_base: int,
) -> list[str]:
    errors: list[str] = []
    for name, val in values.items():
        if val is None:  # type: ignore[comparison-overlap]
            continue
        offset = NAMED_VALUE_OFFSETS.get(name, 0)
        component_id = component_id_base + offset
        body = _nvf_payload(name, val, header_system_id, component_id)
        try:
            r = await client.post(post_url, json=body, timeout=2.0)
            if r.status_code >= 400:
                errors.append(f"{name}: HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            errors.append(f"{name}: {e}")
    return errors
