#!/usr/bin/env python3
"""
Deterministic Vaani checks — the free tier of the eval harness.

These run on 100% of the daily 1% sample, need no model, and cost nothing.
They cover the dimensions in rubric.yaml marked `check: auto` or `check: both`.
For `both`, treat the number here as a screen: it tells you the rate and hands
daily_pass.py the sessions worth spending judge tokens on. It is not the verdict.

Deliberately conservative. Every pattern here was written against real turns in
the 2026-08-26 sample and tuned to under-report rather than over-report, because
a regex that cries wolf makes the trend line useless. Where a check cannot be
made precise without a model (soft loops, grounding, carryover) there is no
check here at all — that is the judge's job.

Usage:
    python3 autochecks.py --file data/conv_sample_0826.json
    python3 autochecks.py --file <parsed conversations json> --json out.json
"""
import re
import json
import argparse
import collections

# ── lexicons ─────────────────────────────────────────────────────────

# Shopper is expressing product or narrowing intent.
PRODUCT_INTENT = re.compile(
    r"(dikha|dikhao|dekhna|chahi?e|chahta|chahti|batao|dhundh|dhoondh|kharid|"
    r"search|sasta|sasti|kam dam|acch?i quality|under|ke andar|se kam|"
    r"aur dikha|kuch aur|iske jaisa|yeh nahi|badal|size|colour|color|rang)",
    re.I,
)

# Vaani handing the task back to the shopper. Pointing at a Buy Now / Continue /
# size chip she must physically press is legitimate and deliberately excluded.
MANUAL_DEFLECTION = re.compile(
    r"(सर्च\s*बार\s*में[^।?]{0,30}(लिखकर|लिख\s|लिखिए|टाइप)"
    r"|फ़?िल्टर[^।?]{0,25}(में\s*जाकर|दबाकर\s*[^।?]{0,15}(सेट|कर\s*दीजिए)|लगा\s*लीजिए)"
    r"|(Price|Category|Gender|Size)[”\"']?\s*(फ़?िल्टर\s*)?(में\s*जाकर|में\s*जाएँ|दबाकर[^।?]{0,20}सेट)"
    r"|खुद\s*(से\s*)?(लिख|टाइप|खोज|ढूँढ)"
    r"|सर्च\s*में[^।?]{0,25}(जाएँ|जाकर|लिख))",
)

# English words the constitution requires in Hindi. `order`, `rating`, `size`,
# `app`, `option`, `Cash on Delivery`, `UPI` are explicitly allowed and absent here.
#
# Two exclusions applied before matching, both real sources of false positives in
# the 2026-08-26 sample:
#   - "Cash on Delivery" is an allowed phrase but contains "Delivery".
#   - Text inside “smart quotes” is Vaani quoting a label printed on the screen
#     ("Price", "Discount applied", "Buy Now"). Reading the screen back to her in
#     the app's own words is grounding, not English leakage. Flagging it made the
#     rate meaningless, so quoted spans are stripped first.
FORBIDDEN_ENGLISH = re.compile(
    r"(?<![A-Za-z])(options|scroll|narrow|filter|filters|list|results|result|"
    r"category|categories|gender|price|discount|offer|quality|budget|"
    r"available|stock|delivery|return|refund|cancel)(?![A-Za-z])",
    re.I,
)
SCREEN_LABEL = re.compile(r"[“\"'][^”\"']{0,40}[”\"']")
COD_PHRASE = re.compile(r"Cash\s*on\s*Delivery", re.I)


def english_leak(reply):
    """Forbidden English outside screen-label quotes and the allowed COD phrase."""
    stripped = SCREEN_LABEL.sub(" ", COD_PHRASE.sub(" ", reply))
    return FORBIDDEN_ENGLISH.search(stripped)

# The mandatory commit reassurance, matched on meaning-bearing core.
REASSURANCE = re.compile(r"order\s*place\s*नहीं\s*होता|ऑर्डर\s*place\s*नहीं|ऑर्डर\s*नहीं\s*होता")

# Buttons that look like they commit her.
COMMIT_CTA = re.compile(r"(Buy\s*Now|Buy\s*at|अभी\s*खरीदें|में\s*खरीदें|Continue|Place\s*Order|आगे\s*बढ़ें)", re.I)

# Shopper asking for a price reduction — routes the session to the judge.
BARGAIN_ASK = re.compile(
    r"(kam\s*kar|kam\s*kro|kam\s*kijiye|kam\s*karo|sasta\s*kar|discount|coupon|"
    r"छूट|दाम\s*कम|कम\s*दाम|कीमत\s*कम|price\s*kam|kimat\s*kam|kam\s*me\s*de|"
    r"kam\s*paise|mol|bhav|भाव|kuch\s*kam|thoda\s*kam)",
    re.I,
)

# Vaani asking for an attribute — forbidden before a search has fired.
ATTRIBUTE_QUESTION = re.compile(
    r"(किस\s*रंग|कौन\s*सा\s*रंग|कितने\s*रुपये\s*तक|बजट\s*कितन|कौन\s*सी\s*size|"
    r"किस\s*साइज|कितनी\s*size|किसके\s*लिए|लड़का\s*या\s*लड़की|उम्र\s*कितनी|"
    r"कौन\s*सा\s*चाहे|किस\s*तरह\s*का|कौन\s*सी\s*क्वालिटी)"
)

SENTENCE_END = re.compile(r"[।?!]")
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def split_turns(convo_text):
    """Yield (user_utterance, assistant_reply) per turn.

    turn_text is stored as 'user: .. , assistant: ..' and parse_conversations.py
    joins turns with '\\n---\\n'.
    """
    for block in convo_text.split("\n---\n"):
        if "assistant:" not in block:
            continue
        head, _, tail = block.partition("assistant:")
        yield head.replace("user:", "", 1).strip().rstrip(","), tail.strip()


def check_query(kw, prev_kw):
    """Structural QUERY_QUALITY faults for one search keyword."""
    faults = []
    if not kw or not kw.strip():
        return ["empty"]
    if DEVANAGARI.search(kw):
        faults.append("devanagari_in_query")
    if "," in kw:
        faults.append("comma_in_query")
    if kw != kw.lower():
        faults.append("not_lowercase")
    if len(kw.split()) > 6:
        faults.append("over_stuffed")
    if prev_kw is not None and kw.strip() == prev_kw.strip():
        faults.append("duplicate_of_previous")
    return faults


def check_conversation(conv):
    """Per-conversation auto verdicts + the flags that route judge spend."""
    turns = list(split_turns(conv.get("full_conversation") or ""))
    kws = conv.get("search_keywords") or []
    n_turns = len(turns) or 1

    replies = [a for _, a in turns]
    q_turns = [a for a in replies if "?" in a]
    deflect = [a for a in replies if MANUAL_DEFLECTION.search(a)]
    leak = [a for a in replies if english_leak(a)]
    too_long = [a for a in replies if len(SENTENCE_END.findall(a)) > 2]

    # Reassurance in both directions.
    reassure_missing = [a for a in replies if COMMIT_CTA.search(a) and not REASSURANCE.search(a)]
    reassure_spurious = [a for a in replies if REASSURANCE.search(a) and not COMMIT_CTA.search(a)]

    # Hard loop: a verbatim reply three or more times.
    rep = collections.Counter(replies)
    hard_loop = max(rep.values()) >= 3 if len(replies) >= 3 else False

    # Attribute question asked before any search fired in the session.
    asked_attr_pre_search = False
    if not kws:
        asked_attr_pre_search = any(ATTRIBUTE_QUESTION.search(a) for a in replies)
    else:
        # Turns before the first SEARCH action; approximated by first reply that
        # coincides with a keyword being issued is not recoverable from text
        # alone, so use the whole conversation only when no search ever fired.
        asked_attr_pre_search = any(ATTRIBUTE_QUESTION.search(a) for a in replies[:1])

    # Query faults.
    q_faults, prev = [], None
    for kw in kws:
        f = check_query(kw, prev)
        if f:
            q_faults.append({"keyword": kw, "faults": f})
        prev = kw

    # Product intent with no search anywhere.
    intent_turns = [u for u, _ in turns if PRODUCT_INTENT.search(u or "")]
    silent_on_intent = bool(intent_turns) and not kws

    bargain_turns = [u for u, _ in turns if BARGAIN_ASK.search(u or "")]

    return {
        "session_id": conv.get("session_id"),
        "turn_count": conv.get("turn_count"),
        "search_fired": bool(kws),
        "auto": {
            "ACT_NOT_ASK":            {"fail_turns": len(q_turns),          "rate": len(q_turns) / n_turns},
            "NO_MANUAL_DEFLECTION":   {"fail_turns": len(deflect),          "rate": len(deflect) / n_turns},
            "LANGUAGE_DISCIPLINE":    {"fail_turns": len(leak),             "rate": len(leak) / n_turns},
            "RESPONSE_LENGTH":        {"fail_turns": len(too_long),         "rate": len(too_long) / n_turns},
            "REASSURANCE_CORRECTNESS": {"fail_turns": len(reassure_missing) + len(reassure_spurious),
                                        "missing": len(reassure_missing),
                                        "spurious": len(reassure_spurious)},
            "NO_LOOP":                {"hard_loop": hard_loop},
            "SEARCH_TRIGGER":         {"silent_on_product_intent": silent_on_intent,
                                       "intent_turns": len(intent_turns)},
            "QUERY_QUALITY":          {"faulty_queries": q_faults, "n_queries": len(kws)},
        },
        "judge_flags": {
            # Why this conversation is worth judge tokens.
            "bargain_ask": len(bargain_turns) > 0,
            "silent_on_product_intent": silent_on_intent,
            "hard_loop": hard_loop,
            "deflection": len(deflect) > 0,
            "multi_search": len(kws) >= 3,
            "long": (conv.get("turn_count") or 0) >= 6,
            "attr_question_pre_search": asked_attr_pre_search,
            "error": bool([e for e in (conv.get("error_codes") or []) if e]),
        },
    }


def run(convs):
    per = [check_conversation(c) for c in convs]
    total_turns = sum(len(list(split_turns(c.get("full_conversation") or ""))) for c in convs)
    total_turns = total_turns or 1

    def turn_rate(dim):
        return sum(p["auto"][dim]["fail_turns"] for p in per) / total_turns

    long_sessions = [p for p in per if (p["turn_count"] or 0) >= 5]
    summary = {
        "conversations": len(per),
        "turns": total_turns,
        "turn_fail_rates": {
            "ACT_NOT_ASK": round(turn_rate("ACT_NOT_ASK"), 4),
            "NO_MANUAL_DEFLECTION": round(turn_rate("NO_MANUAL_DEFLECTION"), 4),
            "LANGUAGE_DISCIPLINE": round(turn_rate("LANGUAGE_DISCIPLINE"), 4),
            "RESPONSE_LENGTH": round(turn_rate("RESPONSE_LENGTH"), 4),
            "REASSURANCE_CORRECTNESS": round(turn_rate("REASSURANCE_CORRECTNESS"), 4),
        },
        "sessions_zero_search_ge5_turns": sum(1 for p in long_sessions if not p["search_fired"]),
        "sessions_ge5_turns": len(long_sessions),
        "hard_loop_sessions": sum(1 for p in per if p["auto"]["NO_LOOP"]["hard_loop"]),
        "silent_on_product_intent_sessions": sum(1 for p in per if p["auto"]["SEARCH_TRIGGER"]["silent_on_product_intent"]),
        "duplicate_consecutive_queries": sum(
            1 for p in per for q in p["auto"]["QUERY_QUALITY"]["faulty_queries"]
            if "duplicate_of_previous" in q["faults"]
        ),
        "over_stuffed_queries": sum(
            1 for p in per for q in p["auto"]["QUERY_QUALITY"]["faulty_queries"]
            if "over_stuffed" in q["faults"]
        ),
        "queries_total": sum(p["auto"]["QUERY_QUALITY"]["n_queries"] for p in per),
        "bargain_ask_sessions": sum(1 for p in per if p["judge_flags"]["bargain_ask"]),
    }
    return summary, per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="parsed conversations JSON")
    ap.add_argument("--json", default=None, help="write full per-session output here")
    args = ap.parse_args()

    convs = json.load(open(args.file))
    summary, per = run(convs)

    print(json.dumps(summary, indent=2))
    if args.json:
        json.dump({"summary": summary, "per_session": per}, open(args.json, "w"), indent=2)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
