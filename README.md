# autoresearch

Autonomous ML experiment runner — Claude Code plugin.  
Give an AI agent a training script and let it experiment overnight.

Inspired by [Karpathy's autoresearch](https://github.com/karpathy/autoresearch), adapted for real-world ML projects: any language (Python, R), any model, any metric. Logs everything to MLflow and creates a GitHub Issue with findings.

## How it works

```
Human writes techplan.md (what the model should do)
  ↓
Developer writes training script (src/02_train.py or .R)
  ↓
autoresearch takes over:
  → creates a branch
  → runs baseline
  → modifies the script (one change at a time)
  → trains, evaluates, keeps or discards
  → repeats until budget exhausted
  → generates report + GitHub Issue
  ↓
Human reviews and decides whether to merge
```

The agent follows a **deep-dive strategy**: exhaust hyperparameters first, then try preprocessing/feature engineering, then (optionally) switch model family. It won't surface-skim across 10 different models in 10 experiments.

## Installation

### As a Claude Code plugin

```bash
claude plugin add https://github.com/intuotechnologies/autoresearch
```

### MCP server dependencies

```bash
cd ~/.claude/plugins/autoresearch/mcp
pip install -r requirements.txt
```

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `MLFLOW_TRACKING_URI` | Recommended | MLflow server URL (default: `https://mlflow.intuoconsulting.com`) |
| `GITHUB_TOKEN` | Optional | For creating GitHub Issues at session end |
| `AUTORESEARCH_PROJECT_ROOT` | Optional | Override project root (default: `cwd`) |

## Usage

### Interactive mode

Open your ML project in Claude Code and say:

```
Read skill.md from the autoresearch plugin. Let's start a research session
on src/02_train.py — optimize RMSE, run 20 experiments.
```

Claude will use the `autoresearch_*` MCP tools to manage the session.

### Autonomous mode (overnight)

Use the researcher subagent:

```
Run the autoresearch researcher agent. It should optimize src/02_train.R
for RMSE over 50 experiments. Log everything to MLflow.
```

### Available MCP tools

| Tool | Description |
|---|---|
| `autoresearch_init` | Create branch, init logbook and session |
| `autoresearch_train` | Run training script, return metrics |
| `autoresearch_keep` | Commit current script as improvement |
| `autoresearch_discard` | Git reset, restore previous version |
| `autoresearch_reflect` | Record what worked/didn't, lesson, next direction |
| `autoresearch_state` | Full experiment state (phase, tested, warnings) |
| `autoresearch_log_mlflow` | Log experiment to MLflow |
| `autoresearch_report` | Generate interactive HTML report |

## Plugin structure

```
autoresearch/
├── plugin.json           # Manifest
├── skill.md              # Research methodology
├── mcp/
│   ├── server.py         # MCP server (~200 lines)
│   └── requirements.txt
├── hooks/
│   ├── phase-guard.sh    # PreToolUse: phase enforcement
│   └── post-train.sh     # PostToolUse: status summary
├── agents/
│   └── researcher.md     # Subagent for overnight mode
└── README.md
```

## Design principles

Inherited from [Karpathy's autoresearch](https://github.com/karpathy/autoresearch):

- **Single file to modify** — the agent only touches the training script
- **Fixed budget** — N experiments, not time-based (projects vary in training time)
- **Keep or discard** — binary decision per experiment, no partial merges
- **Self-contained** — no external orchestrator, everything runs through Claude Code
- **Simplicity criterion** — simpler code wins over marginal metric gains

Added for production ML teams:

- **Deep-dive enforcement** — phases prevent surface-skimming
- **Model switch cooldown** — prevents hopping between model families
- **Stagnation detection** — alerts and untried ideas after 8+ discards
- **Logbook with consolidated lessons** — context survives long sessions
- **MLflow integration** — every experiment is tracked
- **GitHub Issue** — findings delivered to the team

## Project requirements

The training script in your project must:

1. Accept no arguments (configuration is in the file)
2. Print metrics in this format at the end:

```
---
rmse: 52.345678
mae: 42.123456
r2: 0.456789
```

3. Have MLflow logging (recommended but not required)

The project should also have a `techplan.md` describing the model's purpose and constraints.

## License

MIT
