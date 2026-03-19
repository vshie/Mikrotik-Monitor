#!/usr/bin/env python3
"""
Test RouterOS API from any machine on the same L2/L3 network as the radio
(e.g. your laptop on 192.168.2.x reaching 192.168.2.4).

Uses the same routeros-api + login modes as the BlueOS extension.

  pip install routeros-api
  python scripts/test_mikrotik_api.py --host 192.168.2.4 --username admin --password 'YOURPASS'
  python scripts/test_mikrotik_api.py --host 192.168.2.4 --username admin --password 'YOURPASS' --plaintext

RouterOS 6.43+: omit --plaintext (challenge login). Use --plaintext only for old ROS or special setups.
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    p = argparse.ArgumentParser(description="Test MikroTik API + registration tables")
    p.add_argument("--host", default="192.168.2.4", help="RouterOS IP")
    p.add_argument("--port", type=int, default=8728, help="API port (8728 default)")
    p.add_argument("--username", "-u", default="admin")
    p.add_argument("--password", "-p", default="admin")
    p.add_argument(
        "--plaintext",
        action="store_true",
        help="Legacy plaintext /login (RouterOS < 6.43 style). Default is challenge login (6.43+).",
    )
    args = p.parse_args()

    try:
        import routeros_api
    except ImportError:
        print("Install: pip install routeros-api", file=sys.stderr)
        return 1

    print(
        f"Connecting {args.username}@{args.host}:{args.port} "
        f"(plaintext_login={args.plaintext})..."
    )

    pool = None
    try:
        pool = routeros_api.RouterOsApiPool(
            args.host,
            username=args.username,
            password=args.password,
            port=args.port,
            plaintext_login=args.plaintext,
        )
        api = pool.get_api()
        print("Login OK.\n")
    except Exception as e:
        print(f"LOGIN FAILED: {e}", file=sys.stderr)
        print(
            "\nHints: wrong password; API user not allowed; try without --plaintext for ROS 6.43+;",
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
                print(f"  first row keys ({len(keys)}): {', '.join(keys[:20])}{' ...' if len(keys) > 20 else ''}")
        except Exception as e:
            print(f"{path}")
            print(f"  ERROR: {e}")

    try:
        pool.disconnect()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
