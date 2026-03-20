#!/usr/bin/env python3
"""
Test RouterOS API from any machine on the same L2/L3 network as the radio
(e.g. your laptop on 192.168.2.x reaching 192.168.2.4).

Uses the same routeros-api + login modes as the BlueOS extension.

  pip install routeros-api

  # Factory default is often admin with NO password — omit -p and env, or:
  python scripts/test_mikrotik_api.py --host 192.168.2.4 --username admin

  # If you set a password in Winbox/WebFig:
  export MIKROTIK_API_PASSWORD='your-secret'
  python scripts/test_mikrotik_api.py --host 192.168.2.4 -u admin

  python scripts/test_mikrotik_api.py --host 192.168.2.4 -u admin -p 'your-secret'

  python scripts/test_mikrotik_api.py ... --plaintext   # only for old ROS / special cases
  python scripts/test_mikrotik_api.py ... --ssl --port 8729   # if only api-ssl is enabled

RouterOS 6.43+: omit --plaintext (challenge login).
"""
from __future__ import annotations

import argparse
import os
import sys

def _login_modes_for_script(force_plaintext: bool, password: str) -> list[bool]:
    """Match app/mikrotik_client.py (empty password → try plaintext-style /login first)."""
    if force_plaintext:
        return [True]
    if password == "":
        return [True, False]
    return [False, True]


_PLACEHOLDER_PASSWORDS = frozenset(
    {
        "ACTUAL_ROUTER_PASSWORD",
        "YOURPASS",
        "YOUR_ROUTER_PASSWORD",
        "YOUR_REAL_ROUTER_PASSWORD",
    }
)


def _resolve_password(arg_password: str | None) -> str:
    """Empty string is valid (MikroTik factory default: admin, no password)."""
    if arg_password is not None:
        return arg_password
    return os.environ.get("MIKROTIK_API_PASSWORD", "")


def main() -> int:
    p = argparse.ArgumentParser(description="Test MikroTik API + registration tables")
    p.add_argument("--host", default="192.168.2.4", help="RouterOS IP")
    p.add_argument("--port", type=int, default=None, help="8728 plain API, 8729 typical for SSL")
    p.add_argument("--username", "-u", default="admin")
    p.add_argument(
        "--password",
        "-p",
        default=None,
        help="Router password (or set env MIKROTIK_API_PASSWORD)",
    )
    p.add_argument(
        "--plaintext",
        action="store_true",
        help="Legacy plaintext /login (RouterOS < 6.43 style). Default is challenge login (6.43+).",
    )
    p.add_argument(
        "--ssl",
        action="store_true",
        help="Use API over TLS (often port 8729). Self-signed cert verification is disabled for this test.",
    )
    args = p.parse_args()

    password = _resolve_password(args.password)

    if password in _PLACEHOLDER_PASSWORDS:
        print(
            f'ERROR: password looks like a README placeholder ("{password}"). '
            "Use the real password you use in Winbox/WebFig for this router.",
            file=sys.stderr,
        )
        return 1

    port = args.port
    if port is None:
        port = 8729 if args.ssl else 8728

    try:
        import routeros_api
    except ImportError:
        print("Install: pip install routeros-api", file=sys.stderr)
        return 1

    pw_note = "empty password (factory-style)" if password == "" else f"password_len={len(password)}"
    modes = _login_modes_for_script(args.plaintext, password)
    print(
        f"Connecting {args.username}@{args.host}:{port} "
        f"(ssl={args.ssl}, will try plaintext_login={modes}, {pw_note})..."
    )

    pool = None
    api = None
    last_err: Exception | None = None
    for pl in modes:
        try:
            if args.ssl:
                pool = routeros_api.RouterOsApiPool(
                    args.host,
                    username=args.username,
                    password=password,
                    port=port,
                    plaintext_login=pl,
                    use_ssl=True,
                    ssl_verify=False,
                    ssl_verify_hostname=False,
                )
            else:
                pool = routeros_api.RouterOsApiPool(
                    args.host,
                    username=args.username,
                    password=password,
                    port=port,
                    plaintext_login=pl,
                )
            api = pool.get_api()
            print(f"Login OK — plaintext_login={pl}. Querying paths the extension uses:\n")
            break
        except Exception as e:
            last_err = e
            print(f"  try plaintext_login={pl} failed: {e}", file=sys.stderr)
            if pool is not None:
                try:
                    pool.disconnect()
                except Exception:
                    pass
                pool = None

    if api is None:
        print(f"\nLOGIN FAILED after trying {modes}. Last error: {last_err}", file=sys.stderr)
        print(
            "\nRouterOS rejected /login (often shown as error 6).\n"
            "Checklist:\n"
            "  • Empty password: script tries plaintext-style /login first, then challenge (see MikroTik API docs).\n"
            "  • WebFig with admin + no password: leave password empty; do not use -p admin.\n"
            "  • User group must allow API; IP → Services → api enabled on 8728.\n"
            "  • Force legacy only: add --plaintext (single mode).\n",
            file=sys.stderr,
        )
        return 1

    paths = [
        "/interface/wireless/registration-table",
        "/interface/wifiwave2/registration-table",
    ]
    for path in paths:
        try:
            rows = api.get_resource(path).get()
            print(f"{path}")
            print(f"  rows: {len(rows)}")
            if rows:
                keys = sorted(rows[0].keys())
                print(
                    f"  first row keys ({len(keys)}): {', '.join(keys[:20])}{' ...' if len(keys) > 20 else ''}"
                )
        except Exception as e:
            print(f"{path}")
            print(f"  ERROR: {e}")

    try:
        pool.disconnect()
    except Exception:
        pass

    print(
        "\nIf login OK but wireless path shows 0 rows, the station may not be associated to an AP "
        "(registration table empty until linked)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
