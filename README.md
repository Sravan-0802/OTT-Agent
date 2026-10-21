# One user, one agent

A replay agent for the OTT dialect platform. For each viewer it keeps one active goal, publishes at most one home page, and scores the result against the randomized holdout.

The agent is standard-library Python. It does not call a remote model. The pack stays on this machine.

## Replay

From the repository root, with the unpacked pack at `one_user_one_agent_pack`:

```bash
PYTHONPATH=src python3 -m ott_agent replay --pack one_user_one_agent_pack --out sample_run --seed oua-practice
```

That command reads the triggers, writes the decisions, and runs `one_user_one_agent_pack/simulator/simulate_outcomes.py` on `sample_run/actions.jsonl`. The same pack, actions, and seed produce the same outcomes. No API key is required.

`--skip-sim` stops after the decisions and the latency report. `--seed` is forwarded to the simulator. Evaluators can point `--pack` at another unpack of the same zip.

## Tests

```bash
PYTHONPATH=src python3 -m pytest
```

The tests cover a buffered open, a duplicate delivery, a three-way dialect conflict, a user-store timeout, a G03 rule correction that leaves other goals alone, and a model claim with no evidence.

## What the replay writes

| Path | Contents |
|---|---|
| `sample_run/decisions.jsonl` | One record per trigger, including do-nothing |
| `sample_run/actions.jsonl` | Home pages that were actually published |
| `sample_run/viewer_state/` | One file per triggered user: goal, decisions, lessons |
| `sample_run/outcomes.jsonl` | Succeeded, failed, or inconclusive, from events only |
| `sample_run/latency_report.json` | Decision latency against the 60 second budget |
| `sample_run/impact_report.md` | Agent arm versus holdout, per goal |
| `sample_run/final_report.md` | What happened, and which users to read |
| `sample_run/sim/` | Raw simulator events and its own report |

Doing nothing is a decision. It is not an action, so it does not appear in `actions.jsonl`. Holdout users do not appear there either.

## Layout

`src/ott_agent/` holds the agent. `timing.py` recovers event time. `engine.py` decides and publishes. `judge.py` scores outcomes. `cli.py` is the replay command. The pack under `one_user_one_agent_pack/` is the only evidence about users and titles.
