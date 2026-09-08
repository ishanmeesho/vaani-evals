#!/usr/bin/env python3
"""One-off helper: list Metabase databases + their numeric IDs, so you can
find the right MB_DATABASE_ID for .env (the DB that the `gold` schema lives in).

Usage:
    python3 list_databases.py
    python3 list_databases.py --cookie "<paste fresh metabase.SESSION value>"
"""
import argparse
import requests
from mb_auth import get_headers, MB_BASE_URL


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", default=None, help="metabase.SESSION cookie value (overrides .env for this run)")
    args = parser.parse_args()

    headers = get_headers(cookie_override=args.cookie)
    r = requests.get(f"{MB_BASE_URL}/api/database", headers=headers, timeout=30)
    r.raise_for_status()
    payload = r.json()
    databases = payload["data"] if isinstance(payload, dict) and "data" in payload else payload

    print(f"{'ID':<6} {'ENGINE':<12} NAME")
    for db in databases:
        print(f"{db.get('id'):<6} {db.get('engine', ''):<12} {db.get('name', '')}")


if __name__ == "__main__":
    main()
