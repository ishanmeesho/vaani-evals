# Vaani eval — ATTRIBUTE_CARRYOVER

**Search carries every attribute stated so far**  ·  severity `blocker`  ·  weight 12

You are grading one transcript of **Vaani**, the Hindi voice assistant inside the
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

## The criterion

Build the running set of constraints the shopper has stated about the current product up to this turn — product type, colour, size, gender/age, material, price bound, style, quantity. Does the search term (or the filter ops) carry ALL of them that are still in force? An attribute is still in force unless the shopper replaced it, released it, or switched to a new product type.

**Passes when**

- Every in-force stated attribute appears in the query or in a filter op.
- A new product type legitimately resets the attribute set. [SRCH]
- An attribute the shopper explicitly dropped ('koi bhi color') is correctly absent.

**Real failures, from production**

- [OBS] session a0c7ffac: shopper said 'Gulabi aur Bhura' (pink AND brown); query became 'pink handbag under 200' — brown silently dropped.
- [OBS] session a0c7ffac: 'acchi quality' stated alongside a price bound; never became a rating sort or filter.

**How this criterion is commonly mis-scored**

This is the dimension most likely to be scored wrong by a careless judge. Require the judge to write out the running attribute set BEFORE looking at the query, so it cannot rationalise a thin query after the fact.


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


## Output

Return only this JSON. No prose before or after it.

```json
{
  "score": 1,
  "verdict": "pass",
  "reasoning": "one or two sentences saying why",
  "evidence": "Vaani's exact words, quoted, or \"\" if the criterion did not apply"
}
```

- `score` 1 and `verdict` "pass" — the criterion applied and Vaani met it.
- `score` 0 and `verdict` "fail" — the criterion applied and Vaani did not.
- `score` null and `verdict` "n/a" — the conversation gave this criterion no
  chance to fire. Use it only for that. It is **not** a hedge for "hard to
  tell": if the criterion applied and you are unsure, score 0 and say why —
  unsure means Vaani did not make it clear.

Quote Vaani verbatim in `evidence`. Never paraphrase into that field.
