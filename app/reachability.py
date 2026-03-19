from __future__ import annotations

import asyncio
import socket


async def icmp_reachable(host: str, timeout_s: float = 1.0) -> bool:
    def _ping() -> bool:
        try:
            from icmplib import ping

            r = ping(host, count=1, timeout=timeout_s, privileged=False)
            return bool(r.is_alive)
        except Exception:
            return False

    return await asyncio.to_thread(_ping)


async def tcp_reachable(host: str, port: int, timeout_s: float = 1.5) -> bool:
    def _try() -> bool:
        try:
            with socket.create_connection((host, int(port)), timeout=timeout_s):
                return True
        except OSError:
            return False

    return await asyncio.to_thread(_try)


async def radio_reachable(host: str, api_port: int) -> tuple[bool, str]:
    """ICMP if possible; always try TCP to RouterOS API port as fallback."""
    if await icmp_reachable(host):
        return True, "icmp"
    if await tcp_reachable(host, api_port):
        return True, "tcp"
    return False, "unreachable"
