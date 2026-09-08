#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env

cat <<'EOF'

Done. Next:

  1. Get a fresh Metabase session cookie (DevTools -> Application -> Cookies ->
     metabase-main.bi.meeshogcp.in -> metabase.SESSION) and:
       export MB_COOKIE='<value>'

  2. Prove the connection:
       ./venv/bin/python toolkit/list_databases.py --cookie "$MB_COOKIE"

  3. Run a pass — see runbook.md for the full daily procedure:
       RUN=$(date +%F)
       ./venv/bin/python daily_pass.py sample --cookie "$MB_COOKIE" --run $RUN
       ./venv/bin/python daily_pass.py select --run $RUN

EOF
