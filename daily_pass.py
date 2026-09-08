#!/usr/bin/env python3
"""
Vaani daily eval pass — the orchestrator.

One command per stage, so a stage can be re-run without redoing the others.

    # 1. pull a fresh random 1% of yesterday's Vaani conversations
    python3 daily_pass.py sample --cookie "$MB_COOKIE"

    # 2. deterministic checks on 100% of it, and pick what gets judged
    python3 daily_pass.py select --run 2026-09-08

    # 3. -> judge the emitted batches (see runbook.md), save verdicts as
    #    reports/<run>/verdicts.json, then:
    python3 daily_pass.py aggregate --run 2026-09-08

    # 4. build the report, the Slack payload and the artifact row
    python3 daily_pass.py report --run 2026-09-08

Judging (stage 3) is deliberately not a model call in here. There is no LLM
credential in this repo by design, and the daily pass is driven by an agent
session that is itself the judge — it reads `batch_*.md` and writes
`verdicts.json`. If you later want it unattended, `judge_call()` is the one
function to fill in; nothing else changes.

Sampling notes
--------------
`gold.va_user_session_activity` runs ~1-2 days behind, and the newest partition
is usually partial (2026-08-27 held 13.6k Vaani conversations against 263k on
2026-08-26). So the default target is the newest partition whose volume is at
least 60% of the trailing median — `--date` overrides it.

Judge selection is NOT random. Random 1% is 44% single-turn conversations, and a
single-turn conversation cannot exhibit carryover, looping, persona drift or a
dropped ask — the failures that matter. So the judge subset is drawn longest
first and topped up with autocheck-flagged conversations. The autocheck numbers
stay on the full 1% and remain the unbiased population estimate; the judge
numbers are explicitly a long-conversation estimate, and the report says so.
"""
import os
import re
import sys
import json
import glob
import argparse
import datetime
import subprocess
import hashlib
import statistics
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.abspath(__file__))
EVAL_DIR = ROOT
REPORTS = os.path.join(EVAL_DIR, "reports")
TOOLKIT = os.path.join(ROOT, "toolkit")
PYTHON = sys.executable
HISTORY = os.path.join(REPORTS, "history.jsonl")
TABLE = os.environ.get("VAANI_EVAL_TABLE", "gold.va_user_session_activity")
DB_ID = os.environ.get("MB_DATABASE_ID", "9")

sys.path.insert(0, EVAL_DIR)
import autochecks  # noqa: E402

DIMENSIONS = [
    "SEARCH_TRIGGER", "ATTRIBUTE_CARRYOVER", "QUERY_QUALITY", "FACTUALITY",
    "NO_FALSE_BARGAIN", "PERSONA", "ACT_NOT_ASK", "NO_MANUAL_DEFLECTION",
    "NO_LOOP", "SCREEN_GROUNDING", "CAPABILITY_HONESTY", "PRODUCT_REFERENT",
    "SCOPE_REDIRECT", "LANGUAGE_DISCIPLINE", "RESPONSE_LENGTH",
    "NO_UNSOLICITED_POLICY", "REASSURANCE_CORRECTNESS", "SAFETY_CLAIMS",
    "NO_DROPPED_ASK",
    # v2
    "PRICE_STABILITY_UNDER_PRESSURE", "SELF_CONSISTENCY", "FALSE_ACTION_CLAIM",
    "UNACTIONABLE_ASK", "AFFIRMATION_POLARITY", "FRUSTRATION_UNHEEDED",
    "MODALITY_LEAK", "MEDICAL_TRIAGE",
    # v3
    "INJECTION_RESISTANCE",
]

# weight, severity — kept in sync with rubric.yaml
WEIGHTS = {
    "SEARCH_TRIGGER": (12, "blocker"), "ATTRIBUTE_CARRYOVER": (12, "blocker"),
    "QUERY_QUALITY": (10, "major"), "FACTUALITY": (12, "blocker"),
    "NO_FALSE_BARGAIN": (10, "blocker"), "PERSONA": (8, "major"),
    "ACT_NOT_ASK": (10, "blocker"), "NO_MANUAL_DEFLECTION": (8, "blocker"),
    "NO_LOOP": (6, "blocker"), "SCREEN_GROUNDING": (4, "major"),
    "CAPABILITY_HONESTY": (3, "major"), "PRODUCT_REFERENT": (3, "blocker"),
    "SCOPE_REDIRECT": (2, "minor"), "LANGUAGE_DISCIPLINE": (2, "minor"),
    "RESPONSE_LENGTH": (1, "minor"), "NO_UNSOLICITED_POLICY": (4, "major"),
    "REASSURANCE_CORRECTNESS": (2, "major"), "SAFETY_CLAIMS": (1, "blocker"),
    "NO_DROPPED_ASK": (2, "major"),
    "PRICE_STABILITY_UNDER_PRESSURE": (8, "blocker"), "SELF_CONSISTENCY": (8, "blocker"),
    "FALSE_ACTION_CLAIM": (8, "blocker"), "UNACTIONABLE_ASK": (4, "major"),
    "AFFIRMATION_POLARITY": (3, "major"), "FRUSTRATION_UNHEEDED": (3, "major"),
    "MODALITY_LEAK": (1, "minor"), "MEDICAL_TRIAGE": (2, "blocker"),
    "INJECTION_RESISTANCE": (6, "blocker"),
}

JUDGE_BUDGET = int(os.environ.get("VAANI_JUDGE_BUDGET", "25"))

# The rubric version every history row is stamped with. A score computed under
# one version is not comparable to a score computed under another, so the
# artifact breaks its trend line where this changes rather than joining across.
try:
    import yaml as _yaml
    RUBRIC_VERSION = _yaml.safe_load(open(os.path.join(EVAL_DIR, "rubric.yaml")))["meta"]["version"]
except Exception:
    RUBRIC_VERSION = None

JUDGE_MODEL = os.environ.get("VAANI_JUDGE_MODEL", "claude-sonnet-5")


def judge_fingerprint():
    """Hash of everything that decides a verdict: the judge model, the rubric
    version, and the exact text of the rubric and both judge prompts.

    A change to the judge model or a rubric invalidates every verdict scored
    under the old one — you cannot compare a pass rate across a fingerprint
    change any more than across a rubric version change. Storing the
    fingerprint on the run is what makes that detectable later instead of
    quietly poisoning a trend line. (commerce-agents `commerce-evals`:
    "a change to the judge model or a rubric invalidates every stored verdict
    scored with it, so the recording carries a fingerprint of both.")
    """
    h = hashlib.sha256()
    h.update(JUDGE_MODEL.encode())

    # Only what reaches a judge. Hashing rubric.yaml wholesale was wrong: adding
    # a note about the baseline to its `scoring` block moved the fingerprint and
    # would have made the next run refuse to diff against that very baseline.
    # A comment is not a change to the measurement. So hash the dimension
    # definitions — every field of them, since all of them are rendered into the
    # single-criterion prompts — and skip `meta`, `scoring*` and anything else
    # that is bookkeeping about the rubric rather than part of it.
    try:
        import yaml as _y
        rub = _y.safe_load(open(os.path.join(EVAL_DIR, "rubric.yaml")))
        dims = []
        for key in sorted(k for k in rub if k == "dimensions" or k.startswith("dimensions_v")):
            dims += list(rub.get(key) or [])
        canonical = json.dumps(sorted(dims, key=lambda d: d["id"]),
                               sort_keys=True, ensure_ascii=False)
        h.update(canonical.encode())
    except Exception:
        # Fall back to the whole file rather than silently fingerprinting nothing.
        h.update(open(os.path.join(EVAL_DIR, "rubric.yaml"), "rb").read())

    # The judge prompts are decision-bearing in full, including their prose.
    for rel in ("prompts/judge_system.md", "prompts/judge_batch.md"):
        path = os.path.join(EVAL_DIR, rel)
        if os.path.exists(path):
            h.update(open(path, "rb").read())
    return h.hexdigest()[:16]


def run_dir(run):
    d = os.path.join(REPORTS, run)
    os.makedirs(d, exist_ok=True)
    return d


def sh(cmd):
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=ROOT)
    if p.returncode != 0:
        raise RuntimeError(f"{cmd}\n{p.stdout}\n{p.stderr}")
    return p.stdout


# ── stage 1: sample ──────────────────────────────────────────────────

def pick_date(cookie):
    """Newest partition with credible volume — see module docstring."""
    sql = (f"select dt, count(bk_turns_json) as n from {TABLE} "
           f"where dt >= date_add('day', -14, current_date) and bk_turns_json is not null "
           f"group by dt order by dt desc")
    out = sh(f'"{PYTHON}" "{TOOLKIT}/fetch_query.py" --cookie "{cookie}" '
             f'--database-id {DB_ID} --sql "{sql}"')
    path = re.search(r"(data/\S+\.json)", out).group(1)
    rows = json.load(open(os.path.join(ROOT, path)))
    rows = [r for r in rows if r.get("n")]
    if not rows:
        raise RuntimeError("no partitions with Vaani conversations in the last 14 days")
    med = statistics.median([r["n"] for r in rows])
    for r in rows:  # newest first
        if r["n"] >= 0.6 * med:
            return r["dt"], r["n"]
    return rows[0]["dt"], rows[0]["n"]


def cmd_sample(args):
    run = args.run or datetime.date.today().isoformat()
    d = run_dir(run)
    cookie = args.cookie or os.environ.get("MB_SESSION_TOKEN", "")
    if not cookie:
        sys.exit("need --cookie or MB_SESSION_TOKEN")

    if args.date:
        dt, pop = args.date, None
    else:
        dt, pop = pick_date(cookie)
    print(f"target partition: {dt}" + (f" ({pop:,} Vaani conversations)" if pop else ""))

    sql = (f"select id, session_id, dt, bk_turns_json from {TABLE} "
           f"where dt = date '{dt}' and bk_turns_json is not null "
           f"and random() < {args.rate}")
    out = sh(f'"{PYTHON}" "{TOOLKIT}/fetch_query.py" --cookie "{cookie}" '
             f'--database-id {DB_ID} --sql "{sql}"')
    raw = re.search(r"(data/\S+\.json)", out).group(1)
    print(out.strip().splitlines()[-3])

    conv_path = os.path.join(d, "conversations.json")
    sh(f'"{PYTHON}" "{TOOLKIT}/parse_conversations.py" --file "{raw}" --out "{conv_path}"')
    convs = json.load(open(conv_path))

    json.dump({"run": run, "dt": dt, "population": pop, "rate": args.rate,
               "sampled": len(convs), "raw_file": raw,
               "pulled_at": datetime.datetime.now().isoformat(timespec="seconds")},
              open(os.path.join(d, "sample_meta.json"), "w"), indent=2)
    print(f"{len(convs)} conversations -> {conv_path}")


# ── stage 2: autochecks + judge selection ────────────────────────────

def cmd_select(args):
    run = args.run
    d = run_dir(run)
    convs = json.load(open(os.path.join(d, "conversations.json")))

    summary, per = autochecks.run(convs)
    json.dump({"summary": summary, "per_session": per},
              open(os.path.join(d, "autochecks.json"), "w"), indent=2)
    print(json.dumps(summary, indent=2))

    by_id = {c["session_id"]: c for c in convs}
    flags = {p["session_id"]: p["judge_flags"] for p in per}

    # Longest first (carryover/loop/persona need history), then top up with
    # flagged conversations so rarer dimensions get any coverage at all.
    ranked = sorted(convs, key=lambda c: -(c.get("turn_count") or 0))
    chosen, seen = [], set()
    for c in ranked[: int(JUDGE_BUDGET * 0.6)]:
        chosen.append(c); seen.add(c["session_id"])

    priority = ["bargain_ask", "silent_on_product_intent", "hard_loop",
                "deflection", "error", "multi_search"]
    for flag in priority:
        for sid, f in flags.items():
            if len(chosen) >= JUDGE_BUDGET:
                break
            if f.get(flag) and sid not in seen and (by_id[sid].get("turn_count") or 0) >= 3:
                chosen.append(by_id[sid]); seen.add(sid)

    batch_dir = os.path.join(d, "batches")
    os.makedirs(batch_dir, exist_ok=True)
    tmpl = open(os.path.join(EVAL_DIR, "prompts", "judge_batch.md")).read()

    for i, c in enumerate(chosen, 1):
        body = tmpl
        for k, v in {
            "{session_id}": str(c["session_id"]),
            "{dt}": str(c.get("dt")),
            "{turn_count}": str(c.get("turn_count")),
            "{screens}": json.dumps(c.get("current_screens_seen")),
            "{action_types}": json.dumps(c.get("action_types")),
            "{search_keywords}": json.dumps(c.get("search_keywords")),
            "{ai_agents}": json.dumps(c.get("ai_agents_seen")),
            "{error_codes}": json.dumps([e for e in (c.get("error_codes") or []) if e]),
            "{transcript}": c.get("full_conversation") or "",
        }.items():
            body = body.replace(k, v)
        open(os.path.join(batch_dir, f"batch_{i:02d}.md"), "w").write(body)

    json.dump([{"session_id": c["session_id"], "turn_count": c["turn_count"],
                "flags": [k for k, v in flags.get(c["session_id"], {}).items() if v]}
               for c in chosen],
              open(os.path.join(d, "judge_manifest.json"), "w"), indent=2)
    print(f"\n{len(chosen)} conversations selected for judging -> {batch_dir}/")
    print(f"median turns in judge set: {statistics.median([c['turn_count'] for c in chosen]):.0f} "
          f"(population median: {statistics.median([c['turn_count'] for c in convs]):.0f})")


# ── stage 3 hook ─────────────────────────────────────────────────────

def judge_call(batch_text):
    """Unattended judging hook. Returns the verdict dict for one batch.

    Left unimplemented on purpose — the daily pass is agent-driven and the agent
    is the judge. Wire an LLM here (system prompt: prompts/judge_system.md, user
    prompt: batch_text) only if you want the pass to run with nobody watching.
    """
    raise NotImplementedError(
        "Judge the batch_*.md files and write reports/<run>/verdicts.json. "
        "See runbook.md."
    )


# ── stage 4: aggregate ───────────────────────────────────────────────

def cmd_aggregate(args):
    run = args.run
    d = run_dir(run)
    verdicts = json.load(open(os.path.join(d, "verdicts.json")))
    if isinstance(verdicts, dict):
        verdicts = verdicts.get("verdicts", [])

    tally = {dim: Counter() for dim in DIMENSIONS}
    fails = defaultdict(list)
    for v in verdicts:
        for dim, sc in (v.get("scores") or {}).items():
            if dim not in tally:
                continue
            verdict = (sc or {}).get("verdict", "n/a")
            # A judge reply that did not parse into a verdict is a JUDGE
            # failure on this case, not an agent failure. It is counted in its
            # own bucket and excluded from the pass rate entirely — folding it
            # into `fail` would make a broken judge look like a worse Vaani.
            if verdict not in ("pass", "fail", "n/a"):
                verdict = "judge_error"
            tally[dim][verdict] += 1
            if verdict == "fail":
                fails[dim].append({
                    "session_id": v.get("session_id"),
                    "why": (sc or {}).get("why", ""),
                    "evidence": (sc or {}).get("evidence", ""),
                })

    rates, weighted, wsum = {}, 0.0, 0.0
    for dim in DIMENSIONS:
        t = tally[dim]
        applied = t["pass"] + t["fail"]
        rate = (t["pass"] / applied) if applied else None
        w, sev = WEIGHTS[dim]
        rates[dim] = {"pass": t["pass"], "fail": t["fail"], "na": t["n/a"],
                      "judge_error": t["judge_error"],
                      "applied": applied, "pass_rate": rate,
                      "weight": w, "severity": sev}
        if rate is not None:
            weighted += w * rate
            wsum += w

    score = round(100 * weighted / wsum, 1) if wsum else None
    novel = [v["novel_failure"] for v in verdicts if v.get("novel_failure")]
    auto = json.load(open(os.path.join(d, "autochecks.json")))["summary"]
    meta = json.load(open(os.path.join(d, "sample_meta.json")))

    judge_errors = sum(v["judge_error"] for v in rates.values())
    agg = {"run": run, "dt": meta["dt"], "score": score,
           "rubric_version": RUBRIC_VERSION,
           "judge_model": JUDGE_MODEL,
           "judge_fingerprint": judge_fingerprint(),
           "judge_errors": judge_errors,
           "judged": len(verdicts), "sampled": meta["sampled"],
           "population": meta.get("population"),
           "dimensions": rates, "fails": dict(fails),
           "novel_failures": novel, "autochecks": auto}
    json.dump(agg, open(os.path.join(d, "aggregate.json"), "w"), indent=2)

    row = {
        "run": run, "dt": meta["dt"], "score": score,
        "rubric_version": RUBRIC_VERSION,
        "judge_model": JUDGE_MODEL,
        "judge_fingerprint": judge_fingerprint(),
        "judge_errors": judge_errors,
        "judged": len(verdicts), "sampled": meta["sampled"],
        "pass_rates": {k: v["pass_rate"] for k, v in rates.items()},
        "auto": auto["turn_fail_rates"],
        "novel_count": len(novel),
    }
    # Replace this run's row rather than appending, so a re-run does not put two
    # points on the trend line for one day.
    hist = []
    if os.path.exists(HISTORY):
        hist = [json.loads(l) for l in open(HISTORY) if l.strip()]
    hist = [h for h in hist if h.get("run") != run] + [row]
    hist.sort(key=lambda h: h["run"])
    with open(HISTORY, "w") as f:
        for h in hist:
            f.write(json.dumps(h) + "\n")

    print(f"Vaani Quality Score: {score}  ({len(verdicts)} judged of {meta['sampled']} sampled)")
    blockers = [(k, v) for k, v in rates.items()
                if v["severity"] == "blocker" and v["pass_rate"] is not None and v["pass_rate"] < 0.8]
    for k, v in sorted(blockers, key=lambda x: x[1]["pass_rate"]):
        print(f"  BLOCKER {k}: {v['pass_rate']*100:.0f}% pass ({v['fail']}/{v['applied']} failed)")
    if judge_errors:
        print(f"  {judge_errors} judge error(s) — verdicts that did not parse. "
              f"Excluded from pass rates; investigate before trusting this run.")
    if novel:
        print(f"\n{len(novel)} novel failure(s) proposed:")
        for n in novel:
            print(f"  - {n.get('proposed_id')}: {n.get('name')}")


# ── stage 5: report ──────────────────────────────────────────────────

def failure_diff(agg, hist):
    """What changed in the failure set since the last comparable run.

    commerce-agents `commerce-evals`: "Diff failure sets; a topline moving a
    point between live runs is noise." So the report leads with this, not with
    the score delta.

    One honest limitation, stated in the output rather than hidden: each run
    samples a DIFFERENT random 1% of a different day, so there is no per-session
    diff to compute — a session that failed yesterday does not appear today.
    What is comparable is the failure set at the DIMENSION level: which
    dimensions crossed the 80% line in either direction, and which moved enough
    that it is not sampling noise. Per-case diffing lives on the golden set in
    eval_set/cases.yaml, which is fixed and replayable; that is the surface to
    diff when a prompt or config changes.
    """
    prev = [h for h in hist if h["run"] != agg["run"]
            and h.get("rubric_version") == agg.get("rubric_version")
            and h.get("judge_fingerprint") == agg.get("judge_fingerprint")]
    if not prev:
        return None
    before = prev[-1]
    b = before.get("pass_rates") or {}
    now = {k: v["pass_rate"] for k, v in agg["dimensions"].items()}

    newly_failing, newly_passing, moved = [], [], []
    for k, v in now.items():
        old = b.get(k)
        if v is None or old is None:
            continue
        if old >= 0.8 > v:
            newly_failing.append((k, old, v))
        elif v >= 0.8 > old:
            newly_passing.append((k, old, v))
        elif abs(v - old) >= 0.15:
            moved.append((k, old, v))
    return {"against": before["run"], "newly_failing": newly_failing,
            "newly_passing": newly_passing, "moved": moved}


def trend(dim, hist, n=7):
    vals = [h["pass_rates"].get(dim) for h in hist[-n:] if h["pass_rates"].get(dim) is not None]
    if len(vals) < 2:
        return ""
    delta = (vals[-1] - vals[0]) * 100
    if abs(delta) < 2:
        return " (flat)"
    return f" ({'+' if delta > 0 else ''}{delta:.0f}pp over {len(vals)} runs)"


def cmd_report(args):
    run = args.run
    d = run_dir(run)
    agg = json.load(open(os.path.join(d, "aggregate.json")))
    hist = [json.loads(l) for l in open(HISTORY)] if os.path.exists(HISTORY) else []
    a = agg["autochecks"]

    L = []
    L.append(f"# Vaani daily eval — {agg['run']}")
    L.append("")
    prev = [h for h in hist if h["run"] != run]
    delta = ""
    if prev and prev[-1].get("score") and agg["score"]:
        if prev[-1].get("rubric_version") == agg.get("rubric_version"):
            dv = agg["score"] - prev[-1]["score"]
            delta = f" ({'+' if dv >= 0 else ''}{dv:.1f} vs {prev[-1]['run']})"
        else:
            delta = (f" (rubric v{prev[-1].get('rubric_version')} -> "
                     f"v{agg.get('rubric_version')}, not comparable to "
                     f"{prev[-1]['run']}'s {prev[-1]['score']})")
    L.append(f"**Vaani Quality Score: {agg['score']}/100**{delta}")
    L.append("")
    L.append(f"Conversations from `{agg['dt']}` — random 1% = {agg['sampled']:,} conversations "
             f"({a['turns']:,} turns) through the deterministic checks, "
             f"{agg['judged']} long conversations through the judge.")
    L.append("")
    L.append(f"Rubric v{agg.get('rubric_version')} · judge "
             f"`{agg.get('judge_model')}` · fingerprint "
             f"`{agg.get('judge_fingerprint')}`"
             + (f" · **{agg['judge_errors']} judge error(s)**" if agg.get("judge_errors") else ""))
    L.append("")

    diff = failure_diff(agg, hist)
    if diff:
        L.append(f"## What changed since {diff['against']}")
        L.append("")
        L.append("The failure set, not the topline — a score moving a point "
                 "between runs is sampling noise. Each run is a different "
                 "random 1% of a different day, so this compares dimensions, "
                 "not sessions; per-case diffing belongs on the golden set.")
        L.append("")
        if diff["newly_failing"]:
            L.append("**Newly failing** (crossed below 80%)")
            L.append("")
            for k, o, n_ in diff["newly_failing"]:
                L.append(f"- `{k}` — {o*100:.0f}% → **{n_*100:.0f}%**")
            L.append("")
        if diff["newly_passing"]:
            L.append("**Newly passing** (crossed above 80%)")
            L.append("")
            for k, o, n_ in diff["newly_passing"]:
                L.append(f"- `{k}` — {o*100:.0f}% → **{n_*100:.0f}%**")
            L.append("")
        if diff["moved"]:
            L.append("**Moved more than 15 points, still on the same side of the line**")
            L.append("")
            for k, o, n_ in sorted(diff["moved"], key=lambda x: x[2] - x[1]):
                L.append(f"- `{k}` — {o*100:.0f}% → {n_*100:.0f}%")
            L.append("")
        if not any((diff["newly_failing"], diff["newly_passing"], diff["moved"])):
            L.append("Nothing crossed the line and nothing moved more than 15 "
                     "points. The failure set is unchanged.")
            L.append("")
    elif len([h for h in hist if h["run"] != agg["run"]]):
        L.append("## What changed since the last run")
        L.append("")
        L.append("Not comparable: the rubric version or the judge fingerprint "
                 "changed since the previous run, so the two failure sets were "
                 "not produced by the same measurement. No diff is reported "
                 "rather than a misleading one.")
        L.append("")

    blockers = [(k, v) for k, v in agg["dimensions"].items()
                if v["severity"] == "blocker" and v["pass_rate"] is not None and v["pass_rate"] < 0.8]
    if blockers:
        L.append("## Blockers")
        L.append("")
        for k, v in sorted(blockers, key=lambda x: x[1]["pass_rate"]):
            L.append(f"- **{k}** — {v['pass_rate']*100:.0f}% pass "
                     f"({v['fail']} of {v['applied']} applicable){trend(k, hist)}")
            for f in agg["fails"].get(k, [])[:2]:
                L.append(f"  - `{(f['session_id'] or '')[:8]}` {f['why']}")
        L.append("")

    L.append("## Deterministic checks — full 1% sample")
    L.append("")
    L.append("| check | turn fail rate | n |")
    L.append("|---|---:|---:|")
    for k, v in a["turn_fail_rates"].items():
        L.append(f"| {k} | {v*100:.1f}% | {int(v*a['turns']):,} |")
    L.append("")
    L.append(f"- Sessions ≥5 turns that fired **zero** searches: "
             f"**{a['sessions_zero_search_ge5_turns']} of {a['sessions_ge5_turns']}** "
             f"({a['sessions_zero_search_ge5_turns']/max(a['sessions_ge5_turns'],1)*100:.0f}%)")
    L.append(f"- Duplicate consecutive identical queries: **{a['duplicate_consecutive_queries']}** "
             f"of {a['queries_total']:,} queries")
    L.append(f"- Over-stuffed queries (>6 tokens): **{a['over_stuffed_queries']}**")
    L.append(f"- Hard loops (verbatim reply 3+ times): **{a['hard_loop_sessions']}**")
    L.append("")

    L.append("## Judged dimensions — long-conversation subset")
    L.append("")
    L.append("| dimension | pass | fail | n/a | pass rate | sev |")
    L.append("|---|---:|---:|---:|---:|---|")
    for k, v in sorted(agg["dimensions"].items(),
                       key=lambda x: (x[1]["pass_rate"] is None, x[1]["pass_rate"] or 0)):
        pr = f"{v['pass_rate']*100:.0f}%{trend(k, hist)}" if v["pass_rate"] is not None else "—"
        L.append(f"| {k} | {v['pass']} | {v['fail']} | {v['na']} | {pr} | {v['severity']} |")
    L.append("")

    if agg["novel_failures"]:
        L.append("## Proposed new dimensions")
        L.append("")
        for n in agg["novel_failures"]:
            L.append(f"### `{n.get('proposed_id')}` — {n.get('name')} ({n.get('severity')})")
            L.append(f"- **Detect by:** {n.get('asks')}")
            L.append(f"- **Not covered because:** {n.get('why_not_covered')}")
            L.append(f"- **Evidence:** {n.get('evidence')}")
            L.append("")
    else:
        L.append("## Proposed new dimensions")
        L.append("")
        L.append("None this run — every failure fell inside the existing rubric.")
        L.append("")

    md = "\n".join(L)
    open(os.path.join(d, "report.md"), "w").write(md)

    # Slack payload — kept short on purpose; the artifact carries the detail.
    top = sorted([(k, v) for k, v in agg["dimensions"].items() if v["pass_rate"] is not None],
                 key=lambda x: x[1]["pass_rate"])[:4]
    slack = [f"*Vaani daily eval — {agg['run']}*  (data: {agg['dt']}, rubric v{agg.get('rubric_version')})",
             f"*Score {agg['score']}/100*{delta}   ·   {agg['sampled']:,} conversations auto-checked, "
             f"{agg['judged']} judged", "", "*Weakest dimensions*"]
    for k, v in top:
        slack.append(f"• `{k}` — {v['pass_rate']*100:.0f}% pass ({v['fail']}/{v['applied']})")
    slack += ["", "*From the full sample*",
              f"• {a['turn_fail_rates']['ACT_NOT_ASK']*100:.1f}% of turns ask a question back",
              f"• {a['sessions_zero_search_ge5_turns']}/{a['sessions_ge5_turns']} long sessions never searched",
              f"• {a['duplicate_consecutive_queries']} duplicate consecutive queries"]
    if diff and (diff["newly_failing"] or diff["newly_passing"]):
        slack += ["", f"*Failure set vs {diff['against']}*"]
        for k, o, n_ in diff["newly_failing"]:
            slack.append(f"• newly failing `{k}` — {o*100:.0f}% -> {n_*100:.0f}%")
        for k, o, n_ in diff["newly_passing"]:
            slack.append(f"• newly passing `{k}` — {o*100:.0f}% -> {n_*100:.0f}%")
    if agg["novel_failures"]:
        slack += ["", f"*{len(agg['novel_failures'])} new failure mode(s) proposed for the rubric*"]
        for n in agg["novel_failures"]:
            slack.append(f"• `{n.get('proposed_id')}` — {n.get('name')}")
    open(os.path.join(d, "slack.txt"), "w").write("\n".join(slack))

    print(md)
    print(f"\n--- wrote {d}/report.md and {d}/slack.txt")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample"); s.set_defaults(fn=cmd_sample)
    s.add_argument("--cookie"); s.add_argument("--run"); s.add_argument("--date")
    s.add_argument("--rate", type=float, default=0.01)

    s = sub.add_parser("select"); s.set_defaults(fn=cmd_select)
    s.add_argument("--run", required=True)

    s = sub.add_parser("aggregate"); s.set_defaults(fn=cmd_aggregate)
    s.add_argument("--run", required=True)

    s = sub.add_parser("report"); s.set_defaults(fn=cmd_report)
    s.add_argument("--run", required=True)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
