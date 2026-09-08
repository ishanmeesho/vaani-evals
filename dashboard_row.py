#!/usr/bin/env python3
"""
Build the dashboard's database row for one run.

The published dashboard reads the `runs` collection live, so a run reaches it
by writing one document — no republish, and the URL stays stable. This script
emits that document; the daily pass writes it with the Artifact tool's
`write_db` (collection `runs`, doc_id = the run date).

    python3 dashboard_row.py --run 2026-09-08 --out reports/2026-09-08/db_row.json

Only aggregate figures and the single worst evidence quote per blocker go into
the row. Full transcripts stay local — the store is shared with everyone who
can open the artifact, and these are real shopper utterances.
"""
import os
import json
import argparse
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))


def build(run):
    d = os.path.join(HERE, "reports", run)
    agg = json.load(open(os.path.join(d, "aggregate.json")))
    auto = json.load(open(os.path.join(d, "autochecks.json")))["summary"]
    meta = json.load(open(os.path.join(d, "sample_meta.json")))

    judge_median = pop_median = None
    mpath = os.path.join(d, "judge_manifest.json")
    cpath = os.path.join(d, "conversations.json")
    if os.path.exists(mpath):
        t = [m["turn_count"] for m in json.load(open(mpath)) if m.get("turn_count")]
        judge_median = int(statistics.median(t)) if t else None
    if os.path.exists(cpath):
        t = [c["turn_count"] for c in json.load(open(cpath)) if c.get("turn_count")]
        pop_median = int(statistics.median(t)) if t else None

    # One example per failing blocker — the row the dashboard shows first.
    fails = {}
    for dim, items in (agg.get("fails") or {}).items():
        best = next((f for f in items if f.get("evidence")), None) or (items[0] if items else None)
        if best:
            fails[dim] = [{"session_id": best.get("session_id"),
                           "why": best.get("why", ""),
                           "evidence": best.get("evidence", "")}]

    return {
        "run": agg["run"],
        "dt": agg["dt"],
        "score": agg["score"],
        "rubric_version": agg.get("rubric_version"),
        "judge": os.environ.get("VAANI_JUDGE_MODEL", "claude-sonnet-5"),
        "sampled": meta["sampled"],
        "population": meta.get("population"),
        "judged": agg["judged"],
        "turns": auto["turns"],
        "judge_median_turns": judge_median,
        "population_median_turns": pop_median,
        "sessions_zero_search_ge5": auto["sessions_zero_search_ge5_turns"],
        "sessions_ge5_turns": auto["sessions_ge5_turns"],
        "duplicate_queries": auto["duplicate_consecutive_queries"],
        "over_stuffed": auto["over_stuffed_queries"],
        "queries_total": auto["queries_total"],
        "hard_loops": auto["hard_loop_sessions"],
        "auto": auto["turn_fail_rates"],
        "dimensions": agg["dimensions"],
        "fails": fails,
        "novel": agg.get("novel_failures") or [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    row = build(args.run)
    out = args.out or os.path.join(HERE, "reports", args.run, "db_row.json")
    json.dump(row, open(out, "w"), indent=2, ensure_ascii=False)
    print(f"{out}  ({os.path.getsize(out):,} bytes, score {row['score']}, "
          f"{len(row['novel'])} novel)")


if __name__ == "__main__":
    main()
