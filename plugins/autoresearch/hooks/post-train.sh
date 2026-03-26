#!/usr/bin/env bash
# post-train.sh — PostToolUse hook
#
# Runs after any Bash tool call that contains "autoresearch".
# Auto-updates the logbook timestamp and prints a brief status.

set -euo pipefail

ROOT="${AUTORESEARCH_PROJECT_ROOT:-$(pwd)}"
EXPERIMENTS_JSON="$ROOT/autoresearch/experiments.json"

if [[ ! -f "$EXPERIMENTS_JSON" ]]; then
    exit 0
fi

python3 -c "
import json

data = json.load(open('$EXPERIMENTS_JSON'))
exps = data.get('experiments', [])
total = len(exps)
keeps = sum(1 for e in exps if e.get('status') == 'keep')
discards = sum(1 for e in exps if e.get('status') == 'discard')
crashes = sum(1 for e in exps if e.get('status') == 'crash')

pm = data.get('session', {}).get('primary_metric', 'rmse')
best = None
for e in exps:
    v = (e.get('metrics') or {}).get(pm)
    if v is not None and v > 0:
        if best is None or v < best:
            best = v

best_s = f'{best:.4f}' if best else 'N/A'
print(f'[autoresearch] {total} experiments | {keeps} kept | {discards} discarded | {crashes} crashed | best {pm}: {best_s}')
" 2>/dev/null || true

exit 0
