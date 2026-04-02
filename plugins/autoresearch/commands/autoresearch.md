---
description: Avvia una sessione AutoResearch — ricerca autonoma di esperimenti ML su uno script di training
allowed-tools: Bash, Read, Edit, Glob, Grep, Write
---

# AutoResearch — Autonomous ML Experiment Skill

You are an autonomous ML researcher running experiments on a training script.
Your job: make **one change at a time**, evaluate the result, learn, repeat.

The MCP server `autoresearch` runs on **Cloud Run** and manages state (logbook,
phase tracking, validation). All local operations (training, git, file edits)
are done by you directly.

---

## Project layout (read once, do not re-explore)

```
src/02_train.py   <- Python training script (the ONLY file you modify)
src/02_train.R    <- R training script (alternative — pick one per session)
techplan.md       <- PRD: objective, metric, model families allowed, constraints
src/utils.py      <- shared helpers — read-only, never modify
src/01_preprocess.py <- data pipeline — read-only, never modify
```

**At startup**: ask the user whether to work on Python or R, then read
`techplan.md` and the chosen script **once**. Do not read them again unless
forced (see loop step c).

---

## Setup (before the loop)

```
0. CHECK RESUME: if autoresearch/experiments.json exists locally
   -> read it, call autoresearch_restore(data=<contents>)
   -> skip steps 1-3, go straight to the loop

   If no experiments.json (fresh start):

0b. Ask: Python or R? Read techplan.md + training script (ONCE)

1. Create git branch locally:
   TAG=$(date +%b%d | tr '[:upper:]' '[:lower:]')
   git checkout -b autoresearch/$TAG

2. autoresearch_init(
       branch="autoresearch/$TAG",
       lang="r"|"python",
       primary_metric=<from techplan>,
       direction="minimize"|"maximize"
   )

3. Run baseline training:
   mkdir -p autoresearch
   Bash: Rscript src/02_train.R > run.log 2>&1
   (or: uv run src/02_train.py > run.log 2>&1)
   Parse the --- block from run.log -> extract metrics dict

   autoresearch_baseline(
       metrics={"rmse": 0.123, ...},
       script_snippet=<first 20 lines of training script>
   )
```

---

## Main loop (repeat until budget exhausted)

```
a. autoresearch_state(script_snippet=<first 20 lines of script>)
   -> read: best_metric, phase, guidance, tested list, warnings, untried ideas

b. autoresearch_logbook()
   -> read windowed logbook + consolidated lessons

c. Read training script ONLY if last action was discard or crash-fix
   (after keep, the script is already the edited version — skip read)

d. Edit training script — ONE change, following phase rules below

e. Run training:
   Bash: Rscript src/02_train.R > run.log 2>&1
   (or: uv run src/02_train.py > run.log 2>&1)
   Parse --- block -> metrics dict
   Compare new primary metric vs best_metric from step (a)

f. If metric improved (direction-aware):
     git add src/02_train.R   (or .py)
     git commit -m "autoresearch [<phase>]: <description>"
     SHA=$(git rev-parse --short HEAD)
     autoresearch_keep(
         description="<what changed>",
         phase="hyperparameters"|"preprocessing"|"model_switch",
         metrics={"rmse": 0.115, ...},
         commit_sha=SHA
     )
     <- If server returns status="rejected": git reset --hard HEAD~1
        then call autoresearch_discard (server-side validation failed)

   If metric did NOT improve:
     git checkout src/02_train.R   (or git checkout .)
     autoresearch_discard(
         description="<what you tried>",
         phase="...",
         metrics={"rmse": 0.130, ...}
     )

g. autoresearch_reflect(
       what_worked="...",
       what_didnt_work="...",
       lesson="...",
       next_direction="..."
   )

h. autoresearch_log_mlflow(
       experiment_num=N,
       description="...",
       metrics={...},
       status="keep"|"discard",
       phase="...",
       lesson="..."
   )

i. SAVE STATE locally (the server is in-memory only):
   autoresearch_report() -> save JSON to autoresearch/experiments.json
   autoresearch_logbook() -> save markdown to autoresearch/logbook.md
```

---

## Termination

```
4. autoresearch_report()           -> JSON report summary
5. autoresearch_issue(repo="owner/repo")  -> GitHub Issue via REST API
6. Summarize: best config, key lessons, recommended next steps
```

---

## Parsing metrics from training output

The training script prints a `---` block to stdout:

```
---
rmse: 0.1234
mae: 0.0567
r2: 0.9123
---
```

Parse it with:
```bash
python3 -c "
in_block = False
for line in open('run.log'):
    s = line.strip()
    if s == '---':
        in_block = not in_block
        continue
    if in_block and ':' in s:
        k, v = s.split(':', 1)
        try: print(k.strip(), v.strip())
        except: pass
"
```

Or redirect output to a file: `Rscript src/02_train.R > run.log 2>&1`, then
read `run.log` to extract the block.

---

## Strategy: DEEP-DIVE, not surface-skim

Follow phases **in order**. Do NOT skip ahead.

### Phase 1 — Hyperparameter tuning (minimum 3 experiments)

- Halve AND double key params (n_estimators, max_depth, learning_rate)
- Combine multiple param changes
- Try lesser-known knobs: min_samples_leaf, max_features, bootstrap=False
- Try reducing complexity — simpler often wins on small datasets

### Phase 2 — Preprocessing & feature engineering (minimum 2 experiments)

- **Feature scaling**: StandardScaler or RobustScaler via Pipeline
- **Target transform**: `np.log1p(y)` before training, `np.expm1()` on preds
- **Feature selection**: drop bottom features by importance
- **Interaction terms**: PolynomialFeatures(degree=2, interaction_only=True)
- **Outlier handling**: clip extreme values or use RobustScaler

### Phase 3 — Model family switch (only after Phases 1+2 completed)

Switch model family. Start a NEW deep-dive (3 HP + 2 PP on the new model).

### Cooldown rule

Discarded model switch -> 2 more HP/PP experiments before next switch.

---

## Reflection protocol

After EVERY experiment (keep or discard):
1. **What worked**: specific aspect that helped (or didn't hurt)
2. **What didn't work**: why metric didn't improve (be precise)
3. **Lesson**: concrete takeaway with actual parameter values
4. **Next direction**: specific next experiment to try

---

## Simplicity criterion

All else equal, **simpler is better**. Removing code while keeping equal or
better metrics IS always worth it.

---

## Crash recovery

1. Read the error
2. Fix minimally — don't change model or hyperparameters
3. Re-run training
4. If unfixable: `autoresearch_discard` and try a different approach

---

## Stagnation protocol (8+ consecutive discards)

- Stop small tweaks — check `untried_ideas` from `autoresearch_state`
- Try something radically different from the tested list
- Consider combining preprocessing + hyperparameter change

---

## What NOT to do

- Don't repeat a failed experiment (check tested list)
- Don't add complexity for marginal gains
- Don't skip preprocessing — often the biggest lever
- Don't switch models every 1-2 experiments (deep-dive first)
- Don't remove the `---` metric output block from the training script
- Don't modify files other than the training script
- Don't commit before confirming the metric improved locally
