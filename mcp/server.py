"""
autoresearch MCP server — tools for autonomous ML experimentation.

Tools exposed:
  autoresearch_init       — create branch, init logbook/session
  autoresearch_train      — run training script, return metrics
  autoresearch_keep       — commit current script as improvement
  autoresearch_discard    — git reset, restore previous script
  autoresearch_reflect    — record what worked/didn't, lesson, next direction
  autoresearch_state      — return full experiment state (phase, tested, cooldown)
  autoresearch_log_mlflow — log experiment to MLflow
  autoresearch_report     — generate interactive HTML report

Runs as a stdio MCP server. Claude Code starts it via plugin.json.
"""

from __future__ import annotations

import datetime
import difflib
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("autoresearch")

# ── Session state (lives in process memory) ──────────────────────────────

_session: dict = {}
_experiments: list[dict] = []
_phase_counts: dict[str, int] = {"hyperparameters": 0, "preprocessing": 0, "model_switch": 0}
_switch_cooldown: int = 0
_best_metric: float | None = None
_best_description: str = "baseline"
_consecutive_discards: int = 0
_last_committed_script: str = ""

MIN_HP = 3
MIN_PP = 2
STAGNATION_THRESHOLD = 8
MODEL_SWITCH_COOLDOWN = 2

_MODEL_PATTERNS = [
    ("RandomForest",     re.compile(r"RandomForest|randomForest|ranger", re.I)),
    ("GradientBoosting", re.compile(r"GradientBoosting|gbm|GBRegressor", re.I)),
    ("XGBoost",          re.compile(r"XGB|xgboost|xgb\.", re.I)),
    ("Ridge",            re.compile(r"\bRidge\b")),
    ("Lasso",            re.compile(r"\bLasso\b")),
    ("ElasticNet",       re.compile(r"ElasticNet", re.I)),
    ("SVR",              re.compile(r"\bSVR\b|\bsvm\b", re.I)),
    ("KNN",              re.compile(r"KNeighbors|KNN|knn", re.I)),
]

def _project_root() -> Path:
    return Path(os.environ.get("AUTORESEARCH_PROJECT_ROOT", os.getcwd()))

def _detect_model(script: str) -> str:
    for name, pat in _MODEL_PATTERNS:
        if pat.search(script):
            return name
    return "Unknown"

def _git(args: list[str], **kw):
    return subprocess.run(["git"] + args, cwd=_project_root(),
                          capture_output=True, text=True, **kw)

def _parse_metrics(output: str) -> dict[str, float]:
    metrics, in_block = {}, False
    for line in output.split("\n"):
        s = line.strip()
        if s == "---":
            in_block = True
            continue
        if in_block and ":" in s:
            k, _, v = s.partition(":")
            try:
                metrics[k.strip().lower()] = float(v.strip())
            except ValueError:
                pass
    return metrics


# ── Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def autoresearch_init(
    lang: str = "python",
    primary_metric: str = "rmse",
    direction: str = "minimize",
    max_experiments: int = 10,
    mlflow_experiment: str = "AutoResearch",
    tag: str | None = None,
) -> str:
    """Initialize an autoresearch session: create branch, logbook, and session state."""
    global _session, _experiments, _phase_counts, _switch_cooldown
    global _best_metric, _best_description, _consecutive_discards, _last_committed_script

    root = _project_root()
    tag = tag or datetime.date.today().strftime("%b%d").lower()
    branch = f"autoresearch/{tag}"

    if _git(["rev-parse", "--verify", branch]).returncode == 0:
        for i in range(2, 100):
            candidate = f"{branch}-{i}"
            if _git(["rev-parse", "--verify", candidate]).returncode != 0:
                branch = candidate
                break

    base = _git(["branch", "--show-current"]).stdout.strip()
    _git(["checkout", "-b", branch], check=True)

    scripts = {"python": "src/02_train.py", "r": "src/02_train.R"}
    script_path = scripts.get(lang, scripts["python"])

    _session = {
        "branch": branch, "base_branch": base, "lang": lang,
        "script": script_path, "primary_metric": primary_metric,
        "direction": direction, "max_experiments": max_experiments,
        "mlflow_experiment": mlflow_experiment,
        "started": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    _experiments = []
    _phase_counts = {"hyperparameters": 0, "preprocessing": 0, "model_switch": 0}
    _switch_cooldown = 0
    _best_metric = None
    _best_description = "baseline"
    _consecutive_discards = 0

    ar_dir = root / "autoresearch"
    ar_dir.mkdir(exist_ok=True)
    (ar_dir / "logbook.md").write_text(
        f"# AutoResearch Logbook\n\n**Session**: `{branch}`\n"
        f"**Started**: {_session['started']}\n**Metric**: {primary_metric} ({direction})\n\n---\n\n",
        encoding="utf-8",
    )
    (ar_dir / "experiments.json").write_text(
        json.dumps({"session": _session, "experiments": []}, indent=2), encoding="utf-8"
    )

    return json.dumps({"status": "ok", "branch": branch, "script": script_path})


@mcp.tool()
def autoresearch_train(timeout: int = 300) -> str:
    """Run the training script and return parsed metrics."""
    global _best_metric, _best_description, _last_committed_script

    root = _project_root()
    lang = _session.get("lang", "python")
    cmds = {"python": ["python", _session["script"]], "r": ["Rscript", _session["script"]]}
    cmd = cmds.get(lang, cmds["python"])

    log_path = root / "run.log"
    try:
        with open(log_path, "w") as f:
            result = subprocess.run(cmd, cwd=root, stdout=f, stderr=subprocess.STDOUT, timeout=timeout)
        output = log_path.read_text(encoding="utf-8")
        rc = result.returncode
    except subprocess.TimeoutExpired:
        output, rc = "TIMEOUT: training exceeded time limit", -1

    metrics = _parse_metrics(output)
    pm = _session.get("primary_metric", "rmse")

    if _best_metric is None and pm in metrics:
        _best_metric = metrics[pm]
        _best_description = "baseline"
        _last_committed_script = (root / _session["script"]).read_text(encoding="utf-8")

    return json.dumps({
        "exit_code": rc,
        "metrics": metrics,
        "has_primary": pm in metrics,
        "tail": "\n".join(output.split("\n")[-30:]),
    })


@mcp.tool()
def autoresearch_keep(description: str, phase: str = "hyperparameters", reasoning: str = "") -> str:
    """Mark current script as an improvement — commit and update best."""
    global _best_metric, _best_description, _consecutive_discards
    global _last_committed_script, _switch_cooldown

    root = _project_root()
    script_path = root / _session["script"]
    new_script = script_path.read_text(encoding="utf-8")
    pm = _session["primary_metric"]
    direction = _session["direction"]

    log_path = root / "run.log"
    output = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    metrics = _parse_metrics(output)
    new_val = metrics.get(pm)

    diff_summary = _diff(new_script)

    saved_counts = _phase_counts.copy()
    if phase in _phase_counts:
        _phase_counts[phase] += 1
    if phase == "model_switch":
        _phase_counts["hyperparameters"] = 0
        _phase_counts["preprocessing"] = 0
    elif _switch_cooldown > 0:
        _switch_cooldown -= 1

    _git(["add", _session["script"]], check=True)
    sha_r = _git(["rev-parse", "--short", "HEAD"])
    _git(["commit", "-m", f"autoresearch [{phase}]: {description}"])
    sha = _git(["rev-parse", "--short", "HEAD"]).stdout.strip()

    if new_val is not None:
        _best_metric = new_val
    _best_description = description
    _consecutive_discards = 0
    _last_committed_script = new_script

    entry = {
        "num": len(_experiments) + 1, "description": description,
        "reasoning": reasoning, "phase": phase, "commit": sha,
        "status": "keep", "metrics": metrics, "diff_summary": diff_summary,
        "reflection": None,
    }
    _experiments.append(entry)
    _append_logbook(entry)
    _save_experiments()

    return json.dumps({"status": "keep", "commit": sha, "metrics": metrics})


@mcp.tool()
def autoresearch_discard(description: str = "", phase: str = "hyperparameters", reasoning: str = "") -> str:
    """Discard current changes — git reset, restore script, increment discard counter."""
    global _consecutive_discards, _switch_cooldown

    root = _project_root()
    script_path = root / _session["script"]

    diff_summary = ""
    metrics: dict[str, float] = {}
    if script_path.exists():
        try:
            current = script_path.read_text(encoding="utf-8")
            diff_summary = _diff(current)
        except Exception:
            pass
    log_path = root / "run.log"
    if log_path.exists():
        try:
            metrics = _parse_metrics(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    _git(["checkout", "."])

    saved = _phase_counts.copy()
    if phase in _phase_counts:
        _phase_counts[phase] += 1
    if phase == "model_switch":
        _phase_counts["hyperparameters"] = saved["hyperparameters"]
        _phase_counts["preprocessing"] = saved["preprocessing"]
        _switch_cooldown = MODEL_SWITCH_COOLDOWN
    elif _switch_cooldown > 0:
        _switch_cooldown -= 1

    _consecutive_discards += 1

    entry = {
        "num": len(_experiments) + 1, "description": description or "discarded experiment",
        "reasoning": reasoning, "phase": phase, "commit": "—", "status": "discard",
        "metrics": metrics, "diff_summary": diff_summary, "reflection": None,
    }
    _experiments.append(entry)
    _append_logbook(entry)
    _save_experiments()

    return json.dumps({
        "status": "discard",
        "consecutive_discards": _consecutive_discards,
        "switch_cooldown": _switch_cooldown,
    })


@mcp.tool()
def autoresearch_reflect(
    what_worked: str,
    what_didnt_work: str,
    lesson: str,
    next_direction: str = "",
) -> str:
    """Record reflection on the last experiment. Call AFTER keep/discard."""
    if not _experiments:
        return json.dumps({"status": "error", "error": "No experiments yet"})

    reflection = {
        "what_worked": what_worked,
        "what_didnt_work": what_didnt_work,
        "lesson": lesson,
        "next_direction": next_direction,
    }
    _experiments[-1]["reflection"] = reflection

    _update_logbook_reflection(_experiments[-1])
    _save_experiments()

    return json.dumps({"status": "ok", "experiment": _experiments[-1]["num"]})


@mcp.tool()
def autoresearch_state() -> str:
    """Return full experiment state: best metric, phase progress, tested configs, warnings."""
    root = _project_root()
    script = (root / _session["script"]).read_text(encoding="utf-8") if _session.get("script") else ""
    model = _detect_model(script)
    pm = _session.get("primary_metric", "rmse")
    direction = _session.get("direction", "minimize")

    tested = []
    for e in _experiments:
        val = (e.get("metrics") or {}).get(pm)
        tag = {"keep": " KEEP", "discard": " MISS", "crash": "CRASH", "baseline": " BASE"}.get(e["status"], " MISS")
        metric_s = f"{pm}={val:.4f}" if val is not None else f"{pm}=N/A"
        tested.append(f"  [{tag}] #{e['num']} {e['description'][:60]} → {metric_s}")

    hp = _phase_counts.get("hyperparameters", 0)
    pp = _phase_counts.get("preprocessing", 0)

    if hp < MIN_HP:
        guidance = f"Phase 1 — Hyperparameter tuning ({hp}/{MIN_HP} done)"
    elif pp < MIN_PP:
        guidance = f"Phase 2 — Preprocessing ({pp}/{MIN_PP} done)"
    elif _switch_cooldown > 0:
        guidance = f"MODEL SWITCH ON COOLDOWN ({_switch_cooldown} experiments remaining)"
    else:
        guidance = "Phases 1 & 2 complete. May switch model or keep refining."

    warnings = []
    if _consecutive_discards >= STAGNATION_THRESHOLD:
        warnings.append(f"STAGNATION: {_consecutive_discards} consecutive discards! Try something radically different.")
    elif _consecutive_discards >= 3:
        warnings.append(f"WARNING: {_consecutive_discards} consecutive discards.")

    can_switch = hp >= MIN_HP and pp >= MIN_PP and _switch_cooldown == 0

    state = {
        "best_metric": _best_metric,
        "best_description": _best_description,
        "primary_metric": pm,
        "direction": direction,
        "current_model": model,
        "experiments_done": len(_experiments),
        "budget_remaining": _session.get("max_experiments", 10) - len(_experiments),
        "consecutive_discards": _consecutive_discards,
        "phase_counts": _phase_counts,
        "switch_cooldown": _switch_cooldown,
        "can_switch_model": can_switch,
        "guidance": guidance,
        "tested": tested,
        "warnings": warnings,
        "lessons": _build_lessons(),
    }
    return json.dumps(state, indent=2)


@mcp.tool()
def autoresearch_log_mlflow(
    experiment_num: int,
    description: str,
    metrics: dict,
    status: str,
    phase: str = "N/A",
    lesson: str = "",
) -> str:
    """Log an experiment run to MLflow."""
    try:
        import mlflow
        uri = os.environ.get("MLFLOW_TRACKING_URI", "https://mlflow.intuoconsulting.com")
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(_session.get("mlflow_experiment", "AutoResearch"))

        with mlflow.start_run(run_name=f"autoresearch_{experiment_num:03d}") as run:
            mlflow.log_param("experiment_num", experiment_num)
            mlflow.log_param("description", description[:250])
            mlflow.log_param("status", status)
            mlflow.log_param("phase", phase)
            mlflow.log_param("source", "autoresearch")
            for k, v in metrics.items():
                mlflow.log_metric(k, v)
            if lesson:
                mlflow.log_param("lesson", lesson[:250])
            log_path = _project_root() / "run.log"
            if log_path.exists():
                mlflow.log_artifact(str(log_path), artifact_path="logs")
            return json.dumps({"status": "ok", "run_id": run.info.run_id})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


@mcp.tool()
def autoresearch_report() -> str:
    """Generate interactive HTML report from experiments.json."""
    root = _project_root()
    exp_path = root / "autoresearch" / "experiments.json"
    report_path = root / "autoresearch" / "report.html"

    if not exp_path.exists():
        return json.dumps({"status": "error", "error": "experiments.json not found"})

    try:
        import importlib.util
        report_module_path = root / "autoresearch" / "report.py"
        if report_module_path.exists():
            spec = importlib.util.spec_from_file_location("report", report_module_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            path = mod.generate_report(exp_path, report_path)
            return json.dumps({"status": "ok", "path": str(path)})

        return json.dumps({"status": "ok", "path": str(report_path),
                           "note": "report.py not found — use project's report generator"})
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})


# ── Internal helpers ──────────────────────────────────────────────────────

def _diff(new_script: str) -> str:
    if not _last_committed_script:
        return "First modification."
    old = _last_committed_script.splitlines(keepends=True)
    new = new_script.splitlines(keepends=True)
    d = list(difflib.unified_diff(old, new, n=1))
    if not d:
        return "No changes."
    added = [l[1:].strip() for l in d if l.startswith("+") and not l.startswith("+++")]
    removed = [l[1:].strip() for l in d if l.startswith("-") and not l.startswith("---")]
    parts = []
    if removed:
        parts.append("Removed: " + "; ".join(removed[:6]))
    if added:
        parts.append("Added: " + "; ".join(added[:6]))
    return ("\n".join(parts))[:500] or "Minor changes."


def _build_lessons() -> list[str]:
    lessons = []
    for e in _experiments:
        ref = e.get("reflection")
        if isinstance(ref, dict):
            lesson = ref.get("lesson", "")
            if lesson and lesson != "—":
                lessons.append(f"Exp {e['num']} ({e['status']}): {lesson}")
    return lessons


def _append_logbook(entry: dict) -> None:
    root = _project_root()
    lb = root / "autoresearch" / "logbook.md"
    emoji = {"keep": "✅", "discard": "❌", "crash": "💥", "baseline": "📊"}.get(entry["status"], "❓")
    metrics_str = " | ".join(f"**{k}**: {v:.6f}" for k, v in (entry.get("metrics") or {}).items())
    block = (
        f"### Experiment {entry['num']} — {entry['description']} {emoji}\n\n"
        f"**Commit**: `{entry.get('commit', '—')}`  |  "
        f"**Status**: {entry['status']}  |  **Phase**: {entry.get('phase', 'N/A')}\n"
        f"{metrics_str}\n\n"
    )
    if entry.get("reasoning"):
        block += f"**Hypothesis**: {entry['reasoning']}\n\n"
    if entry.get("diff_summary"):
        block += f"**What changed**: {entry['diff_summary']}\n\n"

    ref = entry.get("reflection")
    if ref and isinstance(ref, dict):
        block += _format_reflection(ref)

    block += "---\n\n"
    with open(lb, "a", encoding="utf-8") as f:
        f.write(block)


def _update_logbook_reflection(entry: dict) -> None:
    """Append the reflection block to the logbook for an entry added earlier."""
    root = _project_root()
    lb = root / "autoresearch" / "logbook.md"
    ref = entry.get("reflection")
    if not ref or not isinstance(ref, dict):
        return

    marker = f"### Experiment {entry['num']} —"
    content = lb.read_text(encoding="utf-8")
    idx = content.rfind(marker)
    if idx == -1:
        return

    sep = content.find("\n---\n", idx)
    if sep == -1:
        with open(lb, "a", encoding="utf-8") as f:
            f.write(_format_reflection(ref))
        return

    updated = content[:sep] + "\n" + _format_reflection(ref) + content[sep:]
    lb.write_text(updated, encoding="utf-8")


def _format_reflection(ref: dict) -> str:
    parts = []
    if ref.get("what_worked") and ref["what_worked"] != "—":
        parts.append(f"**What worked**: {ref['what_worked']}\n")
    if ref.get("what_didnt_work") and ref["what_didnt_work"] != "—":
        parts.append(f"**What didn't work**: {ref['what_didnt_work']}\n")
    if ref.get("lesson") and ref["lesson"] != "—":
        parts.append(f"**Lesson learned**: {ref['lesson']}\n")
    if ref.get("next_direction") and ref["next_direction"] != "—":
        parts.append(f"**Next direction**: {ref['next_direction']}\n")
    return "\n".join(parts) + "\n" if parts else ""


def _save_experiments() -> None:
    root = _project_root()
    ep = root / "autoresearch" / "experiments.json"
    data = {"session": _session, "experiments": _experiments}
    ep.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    mcp.run()
