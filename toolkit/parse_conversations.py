#!/usr/bin/env python3
"""
Reconstruct full AI-assistant conversations from `bk_turns_json` in a
fetched gold.va_user_session_activity file.

Each row's bk_turns_json looks like:
    {
      "count": 3,
      "events": [
        {"ts": ..., "action_type": ..., "search_keyword": ..., "current_screen": ...,
         "latency_ms": ..., "turn_text": ..., "product_id": ..., "input_tokens": ...,
         "output_tokens": ..., "ai_agent": ..., "error_code": ...},
        ...
      ]
    }

A single event's "turn_text" is only one turn — the full conversation for
a session is all events' turn_text, concatenated in chronological order.
This script does that, and also rolls up simple per-session stats
(turn count, total tokens, avg latency, error count, agent variants seen)
since those are usually wanted alongside the text itself.

Usage:
    python3 parse_conversations.py                       # newest data/*.json
    python3 parse_conversations.py --file data/foo.json
    python3 parse_conversations.py --min-turns 2          # skip near-empty sessions
"""
import os
import sys
import glob
import json
import argparse
import datetime
from collections import Counter

OUT_DIR = os.environ.get("MB_OUT_DIR", "data")


def latest_json_file():
    files = sorted(glob.glob(os.path.join(OUT_DIR, "session_activity_*.json")))
    if not files:
        print(f"No session_activity_*.json files found in {OUT_DIR}/ — run fetch_query.py first.")
        sys.exit(1)
    return files[-1]


def parse_row(row):
    """Returns None if this row has no usable conversation data."""
    raw = row.get("bk_turns_json")
    if not raw:
        return None
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None

    events = parsed.get("events") or []
    if not events:
        return None

    # Sort chronologically — event order in the JSON isn't guaranteed to be ts order.
    events_sorted = sorted(events, key=lambda e: e.get("ts") or "")

    full_text = "\n---\n".join(e.get("turn_text", "") for e in events_sorted if e.get("turn_text"))
    agents_seen = Counter(e.get("ai_agent") for e in events_sorted if e.get("ai_agent"))
    action_types = Counter(e.get("action_type") for e in events_sorted if e.get("action_type"))
    screens_seen = Counter(e.get("current_screen") for e in events_sorted if e.get("current_screen"))
    search_keywords = [e.get("search_keyword") for e in events_sorted if e.get("search_keyword")]
    product_ids = [e.get("product_id") for e in events_sorted if e.get("product_id")]
    errors = [e.get("error_code") for e in events_sorted if e.get("error_code")]
    latencies = [e["latency_ms"] for e in events_sorted if isinstance(e.get("latency_ms"), (int, float))]
    input_tokens = sum(e.get("input_tokens") or 0 for e in events_sorted)
    output_tokens = sum(e.get("output_tokens") or 0 for e in events_sorted)

    return {
        "session_id": row.get("session_id"),
        "user_id": row.get("id"),
        "dt": row.get("dt"),
        "turn_count": len(events_sorted),
        "full_conversation": full_text,
        "ai_agents_seen": dict(agents_seen),
        "action_types": dict(action_types),
        "current_screens_seen": dict(screens_seen),
        "primary_screen": screens_seen.most_common(1)[0][0] if screens_seen else None,
        "search_keywords": search_keywords,
        "product_ids": product_ids,
        "error_codes": errors,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "total_input_tokens": input_tokens,
        "total_output_tokens": output_tokens,
    }


def parse_file(path, min_turns=1):
    with open(path) as f:
        rows = json.load(f)

    conversations = []
    for row in rows:
        parsed = parse_row(row)
        if parsed and parsed["turn_count"] >= min_turns:
            conversations.append(parsed)

    return conversations, len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", default=None, help="Path to a specific fetched JSON file")
    parser.add_argument("--min-turns", type=int, default=1, help="Skip sessions with fewer turns than this")
    parser.add_argument("--out", default=None, help="Output path (default: data/conversations_<timestamp>.json)")
    args = parser.parse_args()

    path = args.file or latest_json_file()
    print(f"Parsing conversations from: {path}\n")

    conversations, total_rows = parse_file(path, min_turns=args.min_turns)

    print(f"{len(conversations)} of {total_rows} sessions have a conversation with >= {args.min_turns} turn(s).")
    if conversations:
        avg_turns = sum(c["turn_count"] for c in conversations) / len(conversations)
        print(f"Average turns per conversation: {avg_turns:.1f}")

    out_path = args.out
    if not out_path:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUT_DIR, f"conversations_{stamp}.json")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(conversations, f, indent=2, default=str)

    print(f"\nSaved: {out_path}")
    print("Each entry has: session_id, user_id, dt, turn_count, full_conversation, "
          "ai_agents_seen, action_types, error_codes, avg_latency_ms, total_input_tokens, total_output_tokens.")
    print("\nFeed full_conversation per entry to an LLM for judgment-based analysis "
          "(intent classification, sentiment, summarization, etc.) — everything else "
          "here is already computed for you.")


if __name__ == "__main__":
    main()
