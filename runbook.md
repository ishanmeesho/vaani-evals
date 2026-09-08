# The daily hour — runbook

One pass a day, about an hour, same order every time. The point of writing it
down is that the loop should produce a comparable number tomorrow, and a
comparable number is easy to lose by changing two things at once.

Judging is done by **Sonnet reading transcripts** — a language model applying
`prompts/judge_system.md` and one `prompts/langfuse/<DIM>.md` per criterion.
The regexes in `autochecks.py` are not a judge and never produce a verdict on a
judgment dimension: they count things that are literally countable (a `?`, a
duplicate query string, a forbidden English word) and they decide which
conversations are worth a model's attention. Nothing in this harness classifies
intent, quality, or grounding without a model. If you find yourself tempted to
add a trained classifier for one of the judged dimensions, don't — that was
tried elsewhere in this repo and the honest accuracy numbers are in
the mb-session-insights repo (`archive/turn-classifiers/`).

---

## 0. Credential (2 min, or zero — see below)

**For an unattended daily run, use an API key, not a cookie.** A session cookie
expires with the browser session, which means a scheduled pass will stop dead
roughly once a day and wait for a human. `toolkit/mb_auth.py` already supports
`MB_AUTH_MODE=api_key` with `MB_API_KEY`. A Metabase API key has no expiry, so
with one in the environment the loop runs on its own indefinitely.

Getting one is **admin-only** and the key is **group-scoped, not user-scoped**
(confirmed against Metabase's own docs, 2026-09-08). An admin goes to the grid
icon → Admin → Settings → Authentication → API Keys → Manage → Create API Key,
names it, and picks a group — *the key carries that group's permissions, not the
creating admin's*. So unlike the session cookie, which is exactly your own
access, an API key is a new credential whose reach is whatever group it is put
in. Ask for it in a group with read access to the `gold` schema on the Presto
database and nothing more; do not accept one in an admin group for convenience.
Metabase shows the key once and cannot show it again, and it is revoked by
deleting it.

That trade — a non-expiring credential in exchange for group-scoped rather than
personal permissions — is the one decision to make here. It is what makes the
daily pass fully autonomous.

The cookie route, if you would rather not add a credential — fetched fresh each
run and never written to `.env`:

1. Log into `https://metabase-main.bi.meeshogcp.in`
2. DevTools → Application → Cookies → `metabase.SESSION` → copy the value
3. `export MB_COOKIE='<value>'`

For an unattended run on a cookie, set `MB_SESSION_TOKEN` in the Claude Code
environment's variables rather than passing `--cookie`. The scheduled pass picks
it up and runs without asking; when the cookie expires it stops and asks in
`#vaani-eval-report` for a fresh one, which in practice is every day or two.

If the daily trigger fires with neither `MB_API_KEY` nor `MB_SESSION_TOKEN` in
the environment, the run stops at stage 1 and says so. That is the intended
failure — it does not fall back to stale data and it does not report a score it
could not compute.

## 1. Sample (3 min)

```bash
python3 daily_pass.py sample --cookie "$MB_COOKIE"
```

Picks the newest `gold.va_user_session_activity` partition with credible volume
(the table runs 1-2 days behind and the newest partition is usually partial),
pulls a **random 1%** of that day's conversations where `bk_turns_json is not
null`, and parses them. Expect ~2,600 conversations / ~8,000 turns, ~25 seconds.

Force a specific day with `--date 2026-08-26`, a different rate with `--rate`.

## 2. Auto-check and select (1 min)

```bash
python3 daily_pass.py select --run $(date +%F)
```

Runs the deterministic checks on **100%** of the sample — those numbers are the
unbiased population estimate and they are what you quote for prevalence — then
writes `batches/batch_NN.md`, one judge-ready prompt per conversation.

Selection is deliberately **not random**: 44% of a random 1% is single-turn, and
a single-turn conversation cannot show carryover, looping, persona drift or a
dropped ask. So the judge set is drawn longest-first (median ~24 turns against a
population median of 2) and topped up with autocheck-flagged conversations —
price objections, zero-search sessions, hard loops, deflection, errors. The
report labels the judged numbers a long-conversation estimate, because that is
what they are.

Budget: `VAANI_JUDGE_BUDGET=25` by default.

## 3. Judge (35-40 min — the actual work)

Read every `batch_NN.md` and score it. Two ways, same standard:

- **In-session (what day one did).** Read the batches, apply
  `prompts/judge_system.md`, write `reports/<run>/verdicts.json`. The schema is
  at the bottom of `prompts/judge_batch.md`.
- **Per-criterion via Langfuse.** Run each `prompts/langfuse/<DIM>.md` as its
  own evaluator. One criterion per call scores measurably more consistently than
  nineteen at once, and it is how the Langfuse evaluators are configured.

Three failure modes to watch for in your own judging, all seen on day one:

1. **Crediting warmth.** The single most valuable case in the golden set
   (`ASK-001`) is gracious, fluent, correct Hindi and fails two blockers. Long
   friendly replies that make no progress are this product's main defect and the
   easiest thing to over-score.
2. **Reading the prose instead of the query.** `CARRY-001` says
   "गुलाबी और भूरा" and searches `pink handbag under 200`. The sentence is not
   the evidence; `search_keywords` is.
3. **Using `n/a` as a hedge.** `n/a` means the conversation gave the criterion
   no chance to fire. If it applied and you cannot tell, that is a `fail` —
   Vaani did not make it clear.

**Before trusting any movement in the score, check the judge against the golden
set** (`eval_set/cases.yaml`): 20 real conversations with adjudicated verdicts.
Agreement below 85% means the judge drifted, not that Vaani changed. The four
`calibration.hardest_cases` each punish a different lazy heuristic — start
there.

## 4. Aggregate and report (5 min)

```bash
python3 daily_pass.py aggregate --run $(date +%F)
python3 daily_pass.py report    --run $(date +%F)
```

Writes `report.md`, `slack.txt`, appends one row to `reports/history.jsonl`.
The composite is weight-normalised over the dimensions that applied, so `n/a`
never counts as a pass and the absolute weight sum does not matter.

## 5. Grow the rubric (10 min)

Every judged conversation may propose a `novel_failure`. Review each one against
this bar before it enters the rubric:

- Is it genuinely outside all existing dimensions, or a severe instance of one?
  A fabricated price is `FACTUALITY`, not novel.
- Can a judge detect it from a transcript alone, stated as one question?
- Does it have at least one verbatim production quote behind it?
- Would you defend it in a review?

Accepted ones go into `rubric.yaml` under `dimensions_v2` (or a later block)
with a weight, a severity, real `fail_examples`, and the `why_not_covered`
reasoning from the proposal. Then:

```bash
python3 gen_langfuse_prompts.py
python3 push_langfuse.py prompts     # needs LANGFUSE_* keys
```

**Bump `meta.version` when you add dimensions, and never report a delta across a
version boundary.** The composite is normalised so a version bump does not
mechanically move the number, but it changes what is being measured. The
artifact draws a break in the trend line at a version change rather than joining
across it. Day one's 34.4 is a v1 score over 19 dimensions; the first v2 score
is a different measurement.

## 6. Publish (5 min)

```bash
python3 push_langfuse.py scores --run $(date +%F)   # optional
```

Then post `reports/<run>/slack.txt` to Slack and republish the dashboard
artifact with the new `history.jsonl` row. Keep the artifact URL stable — it is
the thing people bookmark.

---

## What day one found, so you know what normal looks like

Baseline from 2026-08-26, n=2,649 conversations / 8,090 turns auto-checked,
25 long conversations judged. **Vaani Quality Score 34.4/100 (rubric v1).**

Every turn in the sample ran on `shopping-assistant` — the **single-layer**
agent. The two-layer v6 stack in `projects/vaani-two-layer/` was not live in
this data, so these numbers measure what is in production now and are the
before-picture for that rollout, not a test of it.

| finding | number |
|---|---|
| turns containing a question back | **98.6%** (7,975 / 8,090) |
| long sessions (≥5 turns) that never searched | **25%** (115 / 459) |
| judged conversations passing `ACT_NOT_ASK` | **0** of 25 |
| duplicate consecutive identical queries | 256 of 2,358 |
| over-stuffed queries (>6 tokens) | 280 |
| turns handing the task back to the shopper | 6.5% |

Eight new failure modes came out of the first pass. The one worth knowing about:
in two independent sessions, the shopper said the item was too expensive and the
next price Vaani quoted was **lower than the one it had just quoted** — ₹999 to
₹196, and ₹452 to ₹279. No bargaining language appears anywhere, so the
bargaining check reports clean; the shopper's experience is that complaining
lowered the price. That is `PRICE_STABILITY_UNDER_PRESSURE`.
