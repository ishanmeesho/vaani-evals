#!/usr/bin/env python3
"""
Pull data from Metabase (default: gold.va_user_session_activity) and save it
locally (JSON + CSV) — nothing leaves your machine except the query itself
going to your own company's Metabase instance.

Feed your session cookie fresh each run with --cookie (it expires, so this
avoids re-editing .env every time). Everything else — base URL, database id,
default table — comes from .env and just works.

Examples:
    # default table, last 2000 rows, cookie fed inline
    python3 fetch_query.py --cookie "abc123..." --limit 2000

    # a date range instead of a flat limit
    python3 fetch_query.py --cookie "abc123..." --start 2026-06-01 --end 2026-07-03

    # fully custom SQL (start/end/limit/table are ignored if --sql is given)
    python3 fetch_query.py --cookie "abc123..." --sql "select * from gold.va_user_session_activity where dt = current_date"

    # fetch AND immediately run the sessions-per-user analysis on the result
    python3 fetch_query.py --cookie "abc123..." --start 2026-06-01 --end 2026-07-03 --analyze --threshold 3
"""
import os
import sys
import csv
import json
import argparse
import datetime
import requests
from dotenv import load_dotenv
from mb_auth import get_headers, MB_BASE_URL
from analyze import analyze_file

load_dotenv()

DEFAULT_DATABASE_ID = os.environ.get("MB_DATABASE_ID", "").strip()
DEFAULT_TABLE = os.environ.get("MB_TABLE", "gold.va_user_session_activity")
DEFAULT_LIMIT = int(os.environ.get("MB_ROW_LIMIT", "50"))
OUT_DIR = os.environ.get("MB_OUT_DIR", "data")


def build_sql(table, start, end, limit):
    where = []
    if start:
        where.append(f"dt >= date('{start}')")
    if end:
        where.append(f"dt <= date('{end}')")
    where_clause = f" where {' and '.join(where)}" if where else ""
    limit_clause = f" limit {limit}" if limit else ""
    return f"select distinct * from {table}{where_clause}{limit_clause}"


MB_ROW_CAP = 1_000_000  # Metabase's own hard cap on rows returned per query
MB_QUERY_TIMEOUT_S = 200  # Metabase itself times out native queries at ~3 min;
                          # we wait a little longer so we get *its* timeout
                          # error back instead of our client cutting it off first


def run_query(sql, headers, database_id):
    payload = {
        "type": "native",
        "native": {"query": sql},
        "database": int(database_id),
        # Metabase's /api/dataset endpoint silently caps "bare row" queries
        # (no aggregation) at 2000 rows by default, regardless of any LIMIT
        # in the SQL itself. Override both constraints up to our own
        # documented cap so a query isn't truncated without a warning.
        "constraints": {
            "max-results": MB_ROW_CAP,
            "max-results-bare-rows": MB_ROW_CAP,
        },
    }
    try:
        r = requests.post(
            f"{MB_BASE_URL}/api/dataset", json=payload, headers=headers, timeout=MB_QUERY_TIMEOUT_S
        )
    except requests.exceptions.Timeout:
        raise SystemExit(
            "Query timed out. Metabase kills native queries after ~3 minutes — "
            "narrow the date range / add filters / query a smaller slice and "
            "combine results locally instead of asking for everything at once."
        )
    r.raise_for_status()
    return r.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cookie", default=None, help="metabase.SESSION cookie value (overrides .env for this run)")
    parser.add_argument("--sql", default=None, help="Full custom SQL (overrides --table/--start/--end/--limit)")
    parser.add_argument("--table", default=DEFAULT_TABLE, help=f"Table to query (default: {DEFAULT_TABLE})")
    parser.add_argument("--start", default=None, help="Start date filter, e.g. 2026-06-01 (inclusive, on dt column)")
    parser.add_argument("--end", default=None, help="End date filter, e.g. 2026-07-03 (inclusive, on dt column)")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Row limit (default: {DEFAULT_LIMIT}); ignored if --start/--end given without an explicit --limit")
    parser.add_argument("--database-id", default=DEFAULT_DATABASE_ID, help="Metabase numeric database id (default: from .env)")
    parser.add_argument("--analyze", action="store_true", help="Run the sessions-per-user analysis on the result immediately")
    parser.add_argument("--threshold", type=float, default=2, help="Threshold to pass to --analyze (default: 2)")
    args = parser.parse_args()

    if not args.database_id:
        print("No database id — pass --database-id or set MB_DATABASE_ID in .env (run list_databases.py to find it).")
        sys.exit(1)

    if args.sql:
        sql = args.sql
    else:
        # If a date range was given but --limit wasn't explicitly set, don't
        # silently cap a date-range pull at the default sample size.
        limit = args.limit if (args.limit != DEFAULT_LIMIT or not (args.start or args.end)) else None
        sql = build_sql(args.table, args.start, args.end, limit)

    print(f"Running query against database {args.database_id}:\n  {sql}\n")

    headers = get_headers(cookie_override=args.cookie)
    result = run_query(sql, headers, args.database_id)

    if result.get("error"):
        print("Metabase returned an error:", result["error"])
        sys.exit(1)

    data = result["data"]
    cols = [c["name"] for c in data["cols"]]
    rows = data["rows"]
    print(f"Got {len(rows)} rows, {len(cols)} columns.")
    if len(rows) >= MB_ROW_CAP:
        print(
            f"WARNING: hit (or exceeded) Metabase's {MB_ROW_CAP:,}-row-per-query cap — "
            "this is very likely truncated, not the full result. Split the pull into "
            "multiple smaller date ranges (or other filters) and combine locally."
        )

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(OUT_DIR, f"session_activity_{stamp}.json")
    csv_path = os.path.join(OUT_DIR, f"session_activity_{stamp}.csv")

    records = [dict(zip(cols, row)) for row in rows]

    with open(json_path, "w") as f:
        json.dump(records, f, indent=2, default=str)

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for rec in records:
            writer.writerow(rec)

    print(f"Saved:\n  {json_path}\n  {csv_path}")

    if args.analyze:
        print()
        analyze_file(json_path, threshold=args.threshold)


if __name__ == "__main__":
    main()
