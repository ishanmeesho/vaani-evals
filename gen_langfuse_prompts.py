#!/usr/bin/env python3
"""
Generate one Langfuse-ready judge prompt per rubric dimension.

Why generated and not hand-written: the rubric grows every day (v1 shipped with
19 dimensions and gained 8 on day one). Hand-maintained per-dimension prompts
would be stale by the second run. This reads rubric.yaml — the single source of
truth — and emits one self-contained single-criterion prompt per dimension, so
adding a dimension to the rubric is the only edit needed.

Each emitted prompt is shaped for Langfuse's LLM-as-a-judge evaluator:
  - mustache {{variables}}, which is what Langfuse interpolates
  - exactly one criterion per prompt, so one score maps to one dimension
  - a numeric score (1 pass / 0 fail) plus reasoning and evidence, which is the
    shape Langfuse's evaluator UI expects
  - `null` score for not-applicable, so n/a is never silently counted as a pass

Outputs:
    prompts/langfuse/<DIMENSION_ID>.md    human-readable, reviewable in git
    prompts/langfuse/prompts.json         API-shaped payloads for push_langfuse.py

Usage:
    python3 gen_langfuse_prompts.py
"""
import os
import json
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "prompts", "langfuse")
PREFIX = os.environ.get("LANGFUSE_PROMPT_PREFIX", "vaani-eval")

# Trimmed from prompts/judge_system.md — the contract every single-criterion
# judge needs, without the multi-dimension procedure that does not apply here.
PREAMBLE = """You are grading one transcript of **Vaani**, the Hindi voice assistant inside the
Meesho shopping app. Her shopper is new to smartphones and new to buying online,
and she is speaking, not typing — so `user:` lines are ASR output and will be
garbled. That is the input Vaani is built for, not an excuse for its behaviour.

You are grading **exactly one criterion**, defined below. Ignore every other
quality of the reply. A conversation can be warm, fluent, perfectly grounded and
still fail this criterion; it can be curt and clumsy and still pass it.

Vaani's own shipping rules, which are the standard you grade against:

- **Voice.** Hindi in Devanagari. English only where the shopper would say it
  herself — order, rating, size, app, option, Cash on Delivery, UPI. Words like
  options, scroll, filter, list, results, category, gender, price must be Hindi.
  Two sentences is the usual size of a reply.
- **Act, then talk.** Do the thing she asked. A question back costs her a turn
  and she may not come back for the second one. Vaani may ask a question only
  where its instructions permit one, and there is exactly one such place: after
  setting a sort from a vague cheap/quality cue whose number is unknown, it may
  ask one question naming two or three concrete numbers.
- **Search first.** Never ask for gender, age, size, colour or budget before
  searching. "saree dikhao" is a complete request. "sasta dikhao" after a kurti
  search is a kurti search. Ask only when there is no product in the utterance,
  on the screen, or in the history.
- **Query format.** One lowercase English phrase, no commas, no Hindi. Product
  type plus only the constraints she stated. Never guess a missing detail.
- **Grounding.** Never state a price, rating, size, date, percentage or policy
  detail Vaani was not given. Point at things by colour and position. If the
  information is missing, say so plainly and move on.
- **Restraint.** Do not narrate your own machinery. Do not preview later steps.
  Do not apologise twice. Before naming a button that looks like it commits her,
  say that pressing it does not place the order.

Two rules about the evidence you are given:

- `search_keywords` is the ground truth of what actually reached search, in
  order. If it is empty, **no search fired**, no matter what Vaani said it was
  doing. Vaani frequently says "मैं ढूँढती हूँ" and fires nothing.
- Garbled input never justifies stalling. Vaani's contract is to take the
  likelier reading and act; the shopper corrects it in five words if wrong.
"""

INPUT_BLOCK = """
## Conversation

```
session_id      : {{session_id}}
turn_count      : {{turn_count}}
screens         : {{screens}}
action_types    : {{action_types}}
search_keywords : {{search_keywords}}
error_codes     : {{error_codes}}
```

```
{{transcript}}
```
"""

OUTPUT_BLOCK = """
## Output

Return only this JSON. No prose before or after it.

```json
{
  "score": 1,
  "verdict": "pass",
  "reasoning": "one or two sentences saying why",
  "evidence": "Vaani's exact words, quoted, or \\"\\" if the criterion did not apply"
}
```

- `score` 1 and `verdict` "pass" — the criterion applied and Vaani met it.
- `score` 0 and `verdict` "fail" — the criterion applied and Vaani did not.
- `score` null and `verdict` "n/a" — the conversation gave this criterion no
  chance to fire. Use it only for that. It is **not** a hedge for "hard to
  tell": if the criterion applied and you are unsure, score 0 and say why —
  unsure means Vaani did not make it clear.

Quote Vaani verbatim in `evidence`. Never paraphrase into that field.
"""


def render(dim):
    L = [f"# Vaani eval — {dim['id']}", ""]
    L.append(f"**{dim['name']}**  ·  severity `{dim['severity']}`  ·  weight {dim['weight']}")
    L.append("")
    L.append(PREAMBLE)
    L.append("## The criterion")
    L.append("")
    if dim.get("rubric"):
        # The decision rule, stated as one PASS condition and one FAIL
        # condition that no reply satisfies both of. This is the thing the
        # judge is answering; everything below is elaboration on it.
        L.append(f"> {dim['rubric'].strip()}")
        L.append("")
    L.append(dim["asks"].strip())
    L.append("")
    if dim.get("pass_when"):
        L.append("**Passes when**")
        L.append("")
        for p in dim["pass_when"]:
            L.append(f"- {p}")
        L.append("")
    if dim.get("fail_examples"):
        L.append("**Real failures, from production**")
        L.append("")
        for f in dim["fail_examples"]:
            L.append(f"- {f}")
        L.append("")
    if dim.get("pass_examples"):
        L.append("**Real passes, from production**")
        L.append("")
        for p in dim["pass_examples"]:
            L.append(f"- {p}")
        L.append("")
    if dim.get("notes"):
        L.append("**How this criterion is commonly mis-scored**")
        L.append("")
        L.append(dim["notes"].strip())
        L.append("")
    if dim.get("why_this_matters"):
        L.append("**Why it is graded at all**")
        L.append("")
        L.append(dim["why_this_matters"].strip())
        L.append("")
    L.append(INPUT_BLOCK)
    L.append(OUTPUT_BLOCK)
    return "\n".join(L)


def main():
    rubric = yaml.safe_load(open(os.path.join(HERE, "rubric.yaml")))
    dims = []
    for key in sorted(k for k in rubric if k == "dimensions" or k.startswith("dimensions_v")):
        dims += list(rubric.get(key) or [])
    os.makedirs(OUT, exist_ok=True)

    payloads = []
    for d in dims:
        body = render(d)
        open(os.path.join(OUT, f"{d['id']}.md"), "w").write(body)
        payloads.append({
            "name": f"{PREFIX}/{d['id'].lower().replace('_', '-')}",
            "type": "text",
            "prompt": body,
            "labels": ["production"],
            "tags": ["vaani", "eval", d["severity"]],
            "config": {
                "dimension": d["id"],
                "severity": d["severity"],
                "weight": d["weight"],
                "check": d.get("check"),
                "rubric_version": rubric["meta"]["version"],
                "model": os.environ.get("VAANI_JUDGE_MODEL", "claude-sonnet-5"),
                "temperature": 0,
                "variables": ["session_id", "turn_count", "screens", "action_types",
                              "search_keywords", "error_codes", "transcript"],
                "score_range": {"pass": 1, "fail": 0, "n/a": None},
            },
        })

    json.dump(payloads, open(os.path.join(OUT, "prompts.json"), "w"), indent=2, ensure_ascii=False)
    print(f"{len(payloads)} prompts -> {OUT}/")
    print(f"manifest: {OUT}/prompts.json")
    for p in payloads:
        print(f"  {p['name']}  ({p['config']['severity']}, w={p['config']['weight']})")


if __name__ == "__main__":
    main()
