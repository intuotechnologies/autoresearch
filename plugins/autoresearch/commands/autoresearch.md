---
description: Avvia una sessione AutoResearch — ricerca autonoma di esperimenti ML su uno script di training
allowed-tools: Bash, Read, Edit, Glob, Grep, Write
---

# AutoResearch — Autonomous ML Experiment Skill

You are an autonomous ML researcher running experiments on a training script.
Your job: make **one change at a time**, evaluate the result, learn, repeat.

This skill provides the complete methodology. The MCP server `autoresearch`
provides the tools (`autoresearch_*`). Use them together.

---

## Workflow

```
1. autoresearch_init            → create branch, init logbook
2. autoresearch_train           → run baseline, get initial metrics
3. LOOP (until budget exhausted):
   a. autoresearch_state        → best metric, phase, cooldown, tested, untried ideas
   b. autoresearch_logbook      → windowed logbook with consolidated lessons
   c. Read the training script
   d. Edit the training script (ONE change, following phase rules below)
   e. autoresearch_train        → run training, get metrics
   f. If improved: autoresearch_keep "description" --phase <phase>
      If not:     autoresearch_discard "description" --phase <phase>
      NOTE: keep validates server-side (is_better + phase). If rejected, discard.
   g. autoresearch_reflect      → record what worked, what didn't, lesson learned
   h. autoresearch_log_mlflow   → log experiment to MLflow
4. autoresearch_report          → generate HTML report
5. autoresearch_issue           → create GitHub Issue with results
```

**IMPORTANT**:
- Always call `autoresearch_reflect` after keep/discard. Without it,
  lessons are lost and the agent repeats mistakes.
- `autoresearch_keep` enforces server-side validation: it rejects if the
  metric didn't actually improve or the phase is invalid. Always check the
  response status.
- Use `autoresearch_logbook` (not raw file read) for context — it windows
  old entries and prepends consolidated lessons.

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
