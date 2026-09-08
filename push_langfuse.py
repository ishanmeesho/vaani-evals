#!/usr/bin/env python3
"""
Push the generated per-criterion judge prompts into Langfuse prompt management,
and optionally push a run's scores back as Langfuse scores.

Where this can run: the Meesho instance at amp-langfuse-web-admin.prd.meesho.int
is on the internal network, so it must be run from a machine on the VPN — a
cloud sandbox has no DNS for it (verified 2026-09-08: NXDOMAIN, while
cloud.langfuse.com answers 200). Langfuse cloud works from anywhere.

    export LANGFUSE_BASE_URL=http://amp-langfuse-web-admin.prd.meesho.int   # or LANGFUSE_HOST
    export LANGFUSE_PUBLIC_KEY=pk-lf-...
    export LANGFUSE_SECRET_KEY=sk-lf-...

    python3 gen_langfuse_prompts.py     # regenerate first
    python3 push_langfuse.py prompts    # upload the 27 prompts
    python3 push_langfuse.py scores --run 2026-09-08

`prompts` creates a new version of each prompt and moves the `production` label
to it — Langfuse versions prompts rather than overwriting, so re-running is safe
and every rubric change is a diffable version bump in Langfuse itself.

`scores` pushes one trace per judged conversation with one score per dimension,
so the Langfuse UI shows per-dimension pass rates over time next to the traces.

No credentials are read from anywhere but the environment, and none are written
to disk. `--dry-run` prints exactly what would be sent and exits.
"""
import os
import sys
import json
import base64
import argparse
import datetime
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "prompts", "langfuse", "prompts.json")


def creds():
    # LANGFUSE_BASE_URL is the name used by the Meesho internal deployment;
    # LANGFUSE_HOST is Langfuse's own documented name. Accept either.
    host = (os.environ.get("LANGFUSE_BASE_URL")
            or os.environ.get("LANGFUSE_HOST")
            or "https://cloud.langfuse.com").rstrip("/")
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    sk = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not pk or not sk:
        sys.exit("Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (and "
                 "LANGFUSE_BASE_URL for a self-hosted instance). Nothing was sent.")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return host, {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}


def call(host, headers, path, payload, method="POST"):
    req = urllib.request.Request(f"{host}{path}", method=method,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:500]}


def cmd_prompts(args):
    payloads = json.load(open(MANIFEST))
    if args.dry_run:
        for p in payloads:
            print(f"POST /api/public/v2/prompts  name={p['name']}  "
                  f"labels={p['labels']}  {len(p['prompt'])} chars")
        print(f"\n{len(payloads)} prompts would be pushed. Nothing sent (--dry-run).")
        return

    host, headers = creds()
    ok, bad = 0, []
    for p in payloads:
        status, body = call(host, headers, "/api/public/v2/prompts", p)
        if status in (200, 201):
            ok += 1
            print(f"  ok   {p['name']}  v{body.get('version', '?')}")
        else:
            bad.append((p["name"], status, body))
            print(f"  FAIL {p['name']}  HTTP {status}  {body.get('error', '')[:160]}")
    print(f"\n{ok} of {len(payloads)} pushed to {host}")
    if bad:
        sys.exit(f"{len(bad)} failed")


def cmd_scores(args):
    run = args.run
    agg_path = os.path.join(HERE, "reports", run, "aggregate.json")
    ver_path = os.path.join(HERE, "reports", run, "verdicts.json")
    agg = json.load(open(agg_path))
    verdicts = json.load(open(ver_path))
    verdicts = verdicts.get("verdicts", verdicts)

    batch = []
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for v in verdicts:
        trace_id = f"vaani-{run}-{v['session_id']}"
        batch.append({"id": f"t-{trace_id}", "type": "trace-create", "timestamp": ts,
                      "body": {"id": trace_id, "name": "vaani-conversation",
                               "timestamp": ts,
                               "sessionId": v["session_id"],
                               "tags": ["vaani", "eval", run],
                               "metadata": {"run": run, "dt": agg["dt"],
                                            "shopper_goal": v.get("shopper_goal"),
                                            "judge": "claude-sonnet-5"},
                               "input": v.get("shopper_goal"),
                               "output": (v.get("worst_turn") or {}).get("quote")}})
        for dim, sc in (v.get("scores") or {}).items():
            verdict = (sc or {}).get("verdict")
            if verdict == "n/a":
                continue
            batch.append({"id": f"s-{trace_id}-{dim}", "type": "score-create", "timestamp": ts,
                          "body": {"traceId": trace_id, "name": dim,
                                   "value": 1 if verdict == "pass" else 0,
                                   "dataType": "NUMERIC",
                                   "comment": (sc.get("why") or "")[:900] or None}})

    if args.dry_run:
        traces = sum(1 for b in batch if b["type"] == "trace-create")
        scores = sum(1 for b in batch if b["type"] == "score-create")
        print(f"POST /api/public/ingestion  {traces} traces, {scores} scores")
        print(json.dumps(batch[:2], indent=2, ensure_ascii=False)[:1200])
        print(f"\nNothing sent (--dry-run).")
        return

    host, headers = creds()
    # Langfuse ingestion accepts batches; keep them small so one bad event does
    # not reject a whole day's push.
    sent = 0
    for i in range(0, len(batch), 50):
        chunk = batch[i:i + 50]
        status, body = call(host, headers, "/api/public/ingestion", {"batch": chunk})
        if status in (200, 201, 207):
            sent += len(chunk)
        else:
            print(f"  FAIL chunk {i//50}  HTTP {status}  {body.get('error','')[:200]}")
    print(f"{sent} of {len(batch)} events pushed to {host}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prompts"); p.set_defaults(fn=cmd_prompts)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("scores"); p.set_defaults(fn=cmd_scores)
    p.add_argument("--run", required=True); p.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
