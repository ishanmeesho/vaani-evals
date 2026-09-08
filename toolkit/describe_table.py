#!/usr/bin/env python3
"""
Show a table's columns + types, so you (or an AI agent operating this repo)
can write correct SQL without guessing column names from a sample row.

Usage:
    python3 describe_table.py --cookie "<paste>"
    python3 describe_table.py --cookie "<paste>" --table gold.some_other_table
"""
import os
import sys
import argparse
from dotenv import load_dotenv
from mb_auth import get_headers, MB_BASE_URL
from fetch_query import run_query, DEFAULT_TABLE, DEFAULT_DATABASE_ID

load_dotenv()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cookie", default=None, help="metabase.SESSION cookie value (overrides .env for this run)")
    parser.add_argument("--table", default=DEFAULT_TABLE, help=f"Table to describe (default: {DEFAULT_TABLE})")
    parser.add_argument("--database-id", default=DEFAULT_DATABASE_ID, help="Metabase numeric database id (default: from .env)")
    args = parser.parse_args()

    if not args.database_id:
        print("No database id — pass --database-id or set MB_DATABASE_ID in .env (run list_databases.py to find it).")
        sys.exit(1)

    headers = get_headers(cookie_override=args.cookie)
    # Presto/Trino syntax — works for any table this repo is pointed at, as
    # long as the underlying engine is Presto (check with list_databases.py).
    result = run_query(f"show columns from {args.table}", headers, args.database_id)

    if result.get("error"):
        print("Metabase returned an error:", result["error"])
        sys.exit(1)

    data = result["data"]
    cols = [c["name"] for c in data["cols"]]
    rows = data["rows"]

    print(f"Columns in {args.table}:\n")
    for row in rows:
        rec = dict(zip(cols, row))
        # Presto's SHOW COLUMNS returns Column/Type/Extra/Comment
        name = rec.get("Column") or rec.get("column") or list(rec.values())[0]
        dtype = rec.get("Type") or rec.get("type") or ""
        print(f"  {name:<30} {dtype}")


if __name__ == "__main__":
    main()
