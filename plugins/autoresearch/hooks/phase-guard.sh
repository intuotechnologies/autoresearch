#!/usr/bin/env bash
# phase-guard.sh — PreToolUse hook
#
# Blocks edits to the training script if the agent is trying to switch model
# family before completing the deep-dive phases. Reads session state from
# the autoresearch MCP server via experiments.json.
#
# Claude Code runs this before any Edit tool call on 02_train.*.
# Exit 0 = allow, exit 2 = block with message.

set -euo pipefail

EXPERIMENTS_JSON="${AUTORESEARCH_PROJECT_ROOT:-$(pwd)}/autoresearch/experiments.json"

if [[ ! -f "$EXPERIMENTS_JSON" ]]; then
    exit 0
fi

HP_COUNT=$(python3 -c "
import json, sys
data = json.load(open('$EXPERIMENTS_JSON'))
exps = data.get('experiments', [])
hp = sum(1 for e in exps if e.get('phase') == 'hyperparameters')
print(hp)
" 2>/dev/null || echo "0")

PP_COUNT=$(python3 -c "
import json, sys
data = json.load(open('$EXPERIMENTS_JSON'))
exps = data.get('experiments', [])
pp = sum(1 for e in exps if e.get('phase') == 'preprocessing')
print(pp)
" 2>/dev/null || echo "0")

if [[ "$HP_COUNT" -lt 3 ]]; then
    echo "Phase guard: $HP_COUNT/3 hyperparameter experiments done. Stay in Phase 1."
fi

if [[ "$HP_COUNT" -ge 3 && "$PP_COUNT" -lt 2 ]]; then
    echo "Phase guard: $PP_COUNT/2 preprocessing experiments done. Work on Phase 2."
fi

exit 0
