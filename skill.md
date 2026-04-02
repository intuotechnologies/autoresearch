# AutoResearch — Autonomous ML Experiment Skill

You are an autonomous ML researcher running experiments on a training script.
Your job: make **one change at a time**, evaluate the result, learn, repeat.

The MCP server `autoresearch` runs on **Cloud Run** and manages state (logbook,
phase tracking, validation). All local operations (training, git, file edits)
are done by you directly using Bash, Read, and Edit.

---

## Workflow

```
0. CHECK RESUME: if autoresearch/experiments.json exists locally
   → read it, call autoresearch_restore(data) to reload server state
   → skip steps 1-4, go straight to the loop

1. Create git branch locally
2. autoresearch_init             → register session on server
3. Run baseline training (Bash)  → parse metrics from --- block
4. autoresearch_baseline         → register baseline metrics on server
5. LOOP (until budget exhausted):
   a. autoresearch_state         → best metric, phase, cooldown, tested, untried ideas
   b. autoresearch_logbook       → windowed logbook with consolidated lessons
   c. Read the training script (only after discard/crash — skip after keep)
   d. Edit the training script (ONE change, following phase rules below)
   e. Run training (Bash)        → parse metrics from --- block
   f. If improved: git commit, then autoresearch_keep (with metrics + commit SHA)
      If not:     git checkout, then autoresearch_discard (with metrics)
      NOTE: keep validates server-side (is_better + phase). If rejected, discard.
   g. autoresearch_reflect       → record what worked, what didn't, lesson learned
   h. autoresearch_log_mlflow    → log experiment to MLflow
   i. SAVE STATE: autoresearch_report() → save to autoresearch/experiments.json
6. autoresearch_report           → JSON report summary
7. autoresearch_issue            → create GitHub Issue with results
```

**IMPORTANT**:
- The server does NOT run training, git, or file operations — you do all of that.
- Always call `autoresearch_reflect` after keep/discard. Without it,
  lessons are lost and the agent repeats mistakes.
- `autoresearch_keep` enforces server-side validation: it rejects if the
  metric didn't actually improve or the phase is invalid. Always check the
  response status. If rejected, undo the commit and call `autoresearch_discard`.
- Use `autoresearch_logbook` (not raw file read) for context — it windows
  old entries and prepends consolidated lessons.

## Local persistence

The Cloud Run server keeps state **in memory only** — it loses everything on
restart. To persist across sessions:

1. **After each iteration** (step 5i): call `autoresearch_report()` and save
   the JSON result to `autoresearch/experiments.json` locally.

2. **On startup** (step 0): if `autoresearch/experiments.json` exists, read it
   and call `autoresearch_restore(data=<contents>)` to reload the server state.
   The server reconstructs everything: session, experiments, phase counts,
   best metric, logbook.

3. **Also save the logbook**: call `autoresearch_logbook()` and save the
   markdown to `autoresearch/logbook.md` — useful for human review.

This way each project has its own `autoresearch/` folder with full history,
and the agent can resume any project from where it left off.

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

Redirect output to a file: `Rscript src/02_train.R > run.log 2>&1`
(or `uv run src/02_train.py > run.log 2>&1`), then parse:

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
        try: print(f'{k.strip()}: {float(v.strip())}')
        except: pass
"
```

Build a metrics dict from the output (e.g. `{"rmse": 0.123, "mae": 0.056, "r2": 0.91}`).

---

## The rules

1. **Modify ONLY the training script.** Nothing else. Not the data pipeline,
   not the evaluation, not the techplan.
2. **ONE change at a time.** Isolate variables. Never change 5 things at once.
3. **Primary metric is shown by `autoresearch_state`.** Lower is better for
   `minimize`, higher for `maximize`.
4. **No new dependencies.** Use only what's already installed.
5. **Keep the code readable.** No obfuscated tricks.
6. **Do NOT remove** the MLflow logging or the structured `---` output block
   that the training script uses to report metrics.

## Simplicity criterion

All else being equal, **simpler is better**. A tiny metric improvement that adds
20 lines of hacky code is NOT worth it. Removing code while keeping equal or
better metrics IS always worth it. Weigh complexity cost against improvement.

---

## Strategy: DEEP-DIVE, not surface-skim

You MUST follow phases in order. Do NOT skip ahead.

### Phase 1 — Hyperparameter tuning (minimum 3 experiments)

Exhaust the current model's hyperparameters FIRST:

- Halve AND double key params (n_estimators, max_depth, learning_rate)
- Combine multiple param changes (lower max_depth + higher n_estimators)
- Try lesser-known knobs: min_samples_leaf, min_samples_split, max_samples,
  max_features="sqrt" vs "log2", warm_start, bootstrap=False
- Try **reducing** complexity (fewer estimators, shallower trees) — simpler
  often wins on small datasets

### Phase 2 — Preprocessing & feature engineering (minimum 2 experiments)

**This is where the biggest gains typically are.** Do NOT skip this.
Concrete ideas (pick ONE per experiment):

- **Feature scaling**: StandardScaler or RobustScaler via Pipeline
- **Target transform**: `np.log1p(y)` before training, `np.expm1()` on preds
- **Feature selection**: drop the bottom 3 features by importance
- **Interaction terms**: PolynomialFeatures(degree=2, interaction_only=True)
- **Outlier handling**: clip extreme values or use RobustScaler

### Phase 3 — Model family switch (only after Phases 1+2 completed)

Switch to a different model family. When you switch, start a NEW deep-dive
(3 more hyperparameter + 2 more preprocessing experiments on the new model).

### Cooldown rule

If a model switch is **discarded** (didn't improve), you must complete 2 more
hyperparameter or preprocessing experiments on the current model before trying
another switch. Don't hop between models — refine what you have.

Check `autoresearch_state` at the start of every iteration. It shows:
- Current phase and progress
- Cooldown status
- Everything already tested (don't repeat)
- Stagnation warnings with untried ideas

---

## Reflection protocol

After EVERY experiment (keep or discard), reflect:

1. **What worked**: the specific aspect that helped (or didn't hurt)
2. **What didn't work**: why the metric didn't improve (be precise)
3. **Lesson**: concrete takeaway with actual parameter values
4. **Next direction**: specific next experiment to try

Write these as a brief note. The logbook persists across experiments and helps
you avoid repeating failed approaches.

---

## Crash recovery

If the training script crashes:

1. Read the error message
2. Fix the bug **minimally** — don't change the model or hyperparameters
3. Re-run training
4. If you can't fix it, `autoresearch_discard` and try a different approach

---

## Stagnation protocol

If you see a stagnation warning (8+ consecutive discards):

- Stop making small tweaks — they aren't working
- Check the "untried ideas" list in `autoresearch_state`
- Try something genuinely different from everything in the tested list
- Consider combining a preprocessing change WITH a hyperparameter change

---

## What NOT to do

- Don't repeat an experiment that already failed (check tested list)
- Don't add unnecessary complexity for marginal gains
- Don't skip preprocessing — it's often the biggest lever
- Don't switch models every 1-2 experiments (deep-dive first)
- Don't remove the metric output block from the training script
- Don't modify files other than the training script
- Don't commit before confirming the metric improved locally
