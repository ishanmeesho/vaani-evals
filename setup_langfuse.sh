#!/usr/bin/env bash
#
# Load the Vaani eval prompts into Langfuse. Run this ONCE from a machine on
# the Meesho VPN.
#
#   ./setup_langfuse.sh                 verify, then push the 28 prompts
#   ./setup_langfuse.sh --with-scores   the same, PLUS a run's traces and scores
#   ./setup_langfuse.sh --dry-run       print what would be sent, send nothing
#
# --with-scores is a superset: it pushes the prompts too, so run one or the
# other, not both. Running both is harmless but creates a second version of
# every prompt for no reason.
#
# Why this has to run from the VPN: amp-langfuse-web-admin.prd.meesho.int is
# internal-only. Verified 2026-09-08 from a cloud sandbox — DNS returns
# NXDOMAIN and the egress gateway answers 502 to CONNECT, while
# metabase-main.bi.meeshogcp.in answers 200 through the same path. So the
# Metabase side of this harness works from anywhere and the Langfuse side does
# not. Nothing to debug; it is a network boundary.
#
# Credentials come from the environment only. Nothing is written to disk and
# nothing is committed. Put them in your shell, or in a .env this script reads:
#
#   LANGFUSE_BASE_URL=http://amp-langfuse-web-admin.prd.meesho.int
#   LANGFUSE_PUBLIC_KEY=pk-lf-...
#   LANGFUSE_SECRET_KEY=sk-lf-...
#
set -euo pipefail
cd "$(dirname "$0")"

DRY=""; SCORES=""; RUN="${RUN:-}"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY="--dry-run" ;;
    --with-scores) SCORES=1 ;;
    --run=*) RUN="${arg#*=}" ;;
    \#*|"#")
      # zsh does not treat # as a comment in an INTERACTIVE shell unless
      # interactive_comments is set, so a line pasted with a trailing comment
      # arrives here as arguments. bash strips it; zsh hands it over.
      echo "Ignoring '$arg' and anything after it — that looks like a pasted"
      echo "trailing comment. zsh does not strip # in an interactive shell."
      break ;;
    *)
      echo "unknown argument: $arg"
      echo
      echo "Usage: ./setup_langfuse.sh [--dry-run] [--with-scores] [--run=YYYY-MM-DD]"
      echo
      echo "If you pasted a command with a trailing '# comment': zsh does not"
      echo "treat # as a comment in an interactive shell. Drop the comment."
      exit 2 ;;
  esac
done

# .env is optional and gitignored; the environment wins over it.
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi

PY="./venv/bin/python"
[ -x "$PY" ] || PY="python3"

: "${LANGFUSE_PUBLIC_KEY:?set LANGFUSE_PUBLIC_KEY (pk-lf-...)}"
: "${LANGFUSE_SECRET_KEY:?set LANGFUSE_SECRET_KEY (sk-lf-...)}"
HOST="${LANGFUSE_BASE_URL:-${LANGFUSE_HOST:-https://cloud.langfuse.com}}"

echo "── 1. Regenerating prompts from rubric.yaml ─────────────────────"
# Always regenerate rather than pushing whatever is on disk: the prompts are
# derived from the rubric, and pushing a stale copy is how the two drift.
$PY gen_langfuse_prompts.py

echo
echo "── 2. Reachability ──────────────────────────────────────────────"
if ! curl -sS -o /dev/null --max-time 15 "$HOST/api/public/health"; then
  echo
  echo "Cannot reach $HOST."
  echo "If that is the internal host, you are not on the VPN — connect and re-run."
  echo "Nothing was sent."
  exit 1
fi
echo "  $HOST reachable"

echo
echo "── 3. Credentials and API version ───────────────────────────────"
$PY push_langfuse.py verify

echo
echo "── 4. Pushing prompts ───────────────────────────────────────────"
$PY push_langfuse.py prompts $DRY

if [ -n "$SCORES" ]; then
  if [ -z "$RUN" ]; then
    # Newest run that has been aggregated.
    RUN=$(ls -1 reports 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort | tail -1)
  fi
  if [ -z "$RUN" ] || [ ! -f "reports/$RUN/aggregate.json" ]; then
    echo
    echo "No aggregated run found to push scores for. Skipping step 5."
    echo "(Pass --run=YYYY-MM-DD, or run daily_pass.py aggregate first.)"
    exit 0
  fi
  echo
  echo "── 5. Pushing traces and scores for run $RUN ────────────────────"
  $PY push_langfuse.py scores --run "$RUN" $DRY
fi

cat <<EOF

Done.

In Langfuse you now have 28 prompts under the vaani-eval/ namespace, each
labelled 'production'. Re-running this creates a new VERSION of each and moves
that label — Langfuse versions prompts rather than overwriting, so every rubric
change becomes a diffable version bump and re-running is safe.

To wire one up as an evaluator: point it at a vaani-eval/<dimension> prompt,
pass the seven variables it declares (session_id, turn_count, screens,
action_types, search_keywords, error_codes, transcript), and read 'score' —
1 pass, 0 fail, null not-applicable. Keep temperature at 0; the prompt's config
records the judge model each rubric version was calibrated against, and
changing either invalidates stored verdicts.
EOF
