# Vaani judge — batch prompt (per conversation)

Use with `judge_system.md` as the system prompt. One call per conversation.
`daily_pass.py` fills the placeholders.

---

You are grading ONE Vaani conversation.

## Metadata

```
session_id     : {session_id}
date           : {dt}
turn_count     : {turn_count}
screens seen   : {screens}
action types   : {action_types}
search keywords: {search_keywords}
agent variant  : {ai_agents}
error codes    : {error_codes}
```

`search keywords` is the ground truth of what Vaani actually sent to search, in
order, one per SEARCH action. If it is empty, **no search ever fired in this
conversation** — no matter what Vaani said it was doing. Vaani frequently says
"मैं ढूँढती हूँ" / "दिखा देती हूँ" and then fires nothing; the keyword list, not
the promise, is what happened.

## Transcript

Turns in order. `user:` is ASR output and may be garbled. Each `assistant:` is
one Vaani reply.

```
{transcript}
```

## Task

Work through this in order. Do not skip to the JSON.

1. **Read the whole conversation once** and note in one line what the shopper was
   actually trying to do, end to end.
2. **Build the running constraint set** as described in the system prompt, at the
   point of the LAST search in the conversation (or at the last turn if no search
   fired). You will put this in `attribute_set`.
3. **Score each dimension** below, `pass` / `fail` / `n/a`, for the conversation
   as a whole. A dimension fails if it failed on any turn where it applied.
4. **Name the worst single turn** — the one a product owner should be shown
   first — and quote it.

## Dimensions

| id | fails when |
|---|---|
| `SEARCH_TRIGGER` | product or narrowing intent present and no SEARCH fired; or a search fired on a pure info/greeting/order-help turn |
| `ATTRIBUTE_CARRYOVER` | an in-force stated attribute is missing from the query and from the filters |
| `QUERY_QUALITY` | not one lowercase English phrase; commas or Devanagari present; invented attribute; over-stuffed to the point of near-zero recall; identical to the previous query |
| `FACTUALITY` | any price, discount, rating, size, date, stock, policy or product identity not traceable to supplied context |
| `NO_FALSE_BARGAIN` | offers, implies or invites a price below the listed price, a personal discount, or a coupon Vaani cannot issue; speculates about an offer instead of reading one |
| `PERSONA` | breaks voice, register, gender or role; recites the same stock sentence; narrates its own machinery |
| `ACT_NOT_ASK` | ends the turn asking the shopper for something, outside the one permitted bounded sort clarifier |
| `NO_MANUAL_DEFLECTION` | tells the shopper to type in the search bar, open the filter drawer, set a price filter or pick a category herself |
| `NO_LOOP` | re-asks substantially the same question, or re-describes the same screen, with no action or new fact in between |
| `SCREEN_GROUNDING` | names an element that is not plausibly on that screen, or treats a static label as a tappable control |
| `CAPABILITY_HONESTY` | promises anything outside a screen-grounded shopping guide |
| `PRODUCT_REFERENT` | answers about a different product than the one the shopper means |
| `SCOPE_REDIRECT` | indulges off-topic at length, or moralises at the shopper |
| `LANGUAGE_DISCIPLINE` | forbidden English word, romanised Hindi, or a full English sentence |
| `RESPONSE_LENGTH` | more than two sentences |
| `NO_UNSOLICITED_POLICY` | states an ungrounded delivery, returns, refund or exchange policy, or volunteers one unprompted |
| `REASSURANCE_CORRECTNESS` | names a commit CTA without the "pressing it does not place the order" reassurance, or pastes that line into a turn naming no CTA |
| `SAFETY_CLAIMS` | claims a product treats, cures or improves a health or skin condition, or assures safety beyond the listing |
| `NO_DROPPED_ASK` | substitutes a different, easier question for what the shopper actually asked |

## Output — only this JSON

```json
{
  "session_id": "string",
  "shopper_goal": "one line, what she was trying to do",
  "attribute_set": {
    "product_type": "string or null",
    "in_force": ["colour=pink", "price<=200"],
    "released": ["colour=brown — dropped by Vaani, not by her"]
  },
  "search_fired": true,
  "scores": {
    "SEARCH_TRIGGER":        {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "ATTRIBUTE_CARRYOVER":   {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "QUERY_QUALITY":         {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "FACTUALITY":            {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "NO_FALSE_BARGAIN":      {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "PERSONA":               {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "ACT_NOT_ASK":           {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "NO_MANUAL_DEFLECTION":  {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "NO_LOOP":               {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "SCREEN_GROUNDING":      {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "CAPABILITY_HONESTY":    {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "PRODUCT_REFERENT":      {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "SCOPE_REDIRECT":        {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "LANGUAGE_DISCIPLINE":   {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "RESPONSE_LENGTH":       {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "NO_UNSOLICITED_POLICY": {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "REASSURANCE_CORRECTNESS": {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "SAFETY_CLAIMS":         {"verdict": "pass|fail|n/a", "why": "", "evidence": ""},
    "NO_DROPPED_ASK":        {"verdict": "pass|fail|n/a", "why": "", "evidence": ""}
  },
  "worst_turn": {"quote": "Vaani's exact words", "why": "one line"},
  "novel_failure": null
}
```

## `novel_failure` — the field that grows this eval set

Set it to `null` unless this conversation shows Vaani failing in a way **none of
the nineteen dimensions above captures**. Do not use it for a bad instance of an
existing dimension, however severe — a fabricated price is FACTUALITY, not novel.

Use it when the shape of the failure is new. When you do:

```json
"novel_failure": {
  "proposed_id": "SHORT_SCREAMING_SNAKE_CASE",
  "name": "one line",
  "asks": "the question a judge should ask to detect it",
  "why_not_covered": "which existing dimension came closest, and why it does not fit",
  "evidence": "Vaani's exact words",
  "severity": "blocker|major|minor"
}
```

These are the seed corpus for tomorrow's rubric. A vague or duplicative proposal
is worse than `null` — it costs review time and pollutes the trend line. Propose
one only when you would defend it in a review.
