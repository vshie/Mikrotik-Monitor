from __future__ import annotations

from typing import Any

import httpx


def _nvf_name_field(name: str) -> list[str]:
    """mavlink2rest (rust-mavlink JSON) expects `name` as 10 single-char strings, null-padded — not a JSON string."""
    ascii_only = name.encode("ascii", errors="ignore").decode("ascii")[:10]
    padded = ascii_only.ljust(10, "\x00")
    return list(padded)


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
            "name": _nvf_name_field(name),
            "value": float(value),
        },
    }


async def send_named_value_floats(
    post_url: str,
    client: httpx.AsyncClient,
    values: dict[str, float],
    header_system_id: int,
    header_component_id: int,
) -> list[str]:
    errors: list[str] = []
    for name, val in values.items():
        if val is None:  # type: ignore[comparison-overlap]
            continue
        body = _nvf_payload(name, val, header_system_id, header_component_id)
        try:
            r = await client.post(post_url, json=body, timeout=2.0)
            if r.status_code >= 400:
                errors.append(f"{name}: HTTP {r.status_code} {r.text[:200]}")
        except Exception as e:
            errors.append(f"{name}: {e}")
    return errors
