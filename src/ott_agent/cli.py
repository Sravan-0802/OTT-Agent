"""Replay the supplied pack and write the sample run."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ott_agent.engine import run_replay, write_outputs
from ott_agent.judge import HORIZON_END, impact_markdown, judge_goal, pilot_table
from ott_agent.timing import iso


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ott-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    replay = sub.add_parser("replay", help="replay triggers and write decisions, outcomes and impact")
    replay.add_argument("--pack", default="one_user_one_agent_pack")
    replay.add_argument("--out", default="sample_run")
    replay.add_argument("--seed", default="oua-practice")
    replay.add_argument("--skip-sim", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "replay":
        return replay_command(Path(args.pack), Path(args.out), args.seed, args.skip_sim)
    return 1


def replay_command(pack: Path, out: Path, seed: str, skip_sim: bool) -> int:
    if not (pack / "replay" / "triggers.jsonl").exists():
        print(f"pack not found at {pack}", file=sys.stderr)
        return 1
    print("replaying triggers...", file=sys.stderr)
    result = run_replay(pack)
    write_outputs(result, out)
    if not skip_sim:
        sim_dir = out / "sim"
        simulator = pack / "simulator" / "simulate_outcomes.py"
        subprocess.check_call(
            [
                sys.executable,
                str(simulator),
                "--pack",
                str(pack),
                "--actions",
                str(out / "actions.jsonl"),
                "--out",
                str(sim_dir),
                "--seed",
                seed,
            ]
        )
        outcomes = judge_run(result, sim_dir / "outcome_events.jsonl")
        _write_jsonl(out / "outcomes.jsonl", outcomes)
        pilot = _pilot(pack)
        report = impact_markdown(result["decisions"], outcomes, pilot, result["triggered_users"])
        (out / "impact_report.md").write_text(report, encoding="utf-8")
        sim_report = {}
        sim_path = sim_dir / "simulator_report.json"
        if sim_path.exists():
            sim_report = json.loads(sim_path.read_text(encoding="utf-8"))
        (out / "final_report.md").write_text(_final(result, outcomes, pilot, sim_report), encoding="utf-8")
    print(f"wrote {out}", file=sys.stderr)
    return 0


def judge_run(result: dict, outcomes_path: Path) -> list[dict]:
    by_user = {}
    if outcomes_path.exists():
        for line in outcomes_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            by_user.setdefault(event["user_id"], []).append(event)
    cat = result["world"]["cat"]
    judged = []
    seen = set()
    for mem in result["memories"].values():
        goals = list(mem.goals_closed)
        if mem.goal is not None:
            goals.append(mem.goal)
        events = by_user.get(mem.user_id, [])
        for goal in goals:
            decision_id = goal.get("decision_id")
            if not decision_id or decision_id in seen:
                continue
            seen.add(decision_id)
            if goal.get("status") == "superseded":
                judged.append(
                    {
                        "viewer_id": mem.user_id,
                        "user_id": mem.user_id,
                        "goal_id": goal["id"],
                        "decision_id": decision_id,
                        "result": "inconclusive",
                        "signal_refs": [],
                        "judged_at": iso(datetime.now(timezone.utc)),
                        "rule_version": goal.get("rule_version"),
                        "notes": "superseded: the plan changed before the deadline, so this goal is not a failure.",
                        "holdout": mem.arm == "holdout",
                        "attributable": False,
                    }
                )
                continue
            outcome = judge_goal(goal, events, cat, HORIZON_END)
            outcome["holdout"] = mem.arm == "holdout"
            judged.append(outcome)
    return judged


def _pilot(pack: Path) -> dict:
    with (pack / "pilot" / "pilot_assignment.csv").open(encoding="utf-8") as handle:
        assignment = list(csv.DictReader(handle))
    with (pack / "pilot" / "pilot_outcomes.csv").open(encoding="utf-8") as handle:
        outcomes = list(csv.DictReader(handle))
    return pilot_table(assignment, outcomes)


def _final(result: dict, outcomes: list[dict], pilot: dict, sim_report: dict | None = None) -> str:
    statuses = Counter(item["status"] for item in result["decisions"])
    actions = Counter(item["action"] for item in result["decisions"] if item["status"] != "DUPLICATE")
    reasons = Counter(item.get("no_action_reason") or "" for item in result["decisions"] if item["action"] == "NO_ACTION")
    results = Counter(item["result"] for item in outcomes)
    lines = [
        "# Replay notes",
        "",
        f"Triggered users: {result['triggered_users']}. Decisions: {len(result['decisions'])}. Published actions: {sum(1 for item in result['decisions'] if item.get('effects_applied'))}.",
        "",
        "## What the agent did",
        "",
        f"- Statuses: {dict(statuses)}",
        f"- Actions, ignoring duplicate deliveries: {dict(actions)}",
        f"- Reasons for doing nothing: { {key: value for key, value in reasons.items() if key} }",
        f"- Judgments: {dict(results)}",
        f"- Open G03 goals updated when the rule was corrected: {len(result['g03_affected'])}. Other users were not replayed.",
        "",
        "G10 (`meaningful_engagement_7d`) has no success event. G11 (`tv_activation_14d`) succeeds on `tv_app_open`, which is not in the stream. Neither is selected.",
        "",
        _simulator_line(sim_report or {}),
        "",
        "The page dialect is the app setting on the live open. Profile dialect and the campaign dialect in a deeplink are recorded as conflicts. A newer profile row does not beat the app setting, and two disagreeing profile rows do not resolve themselves by version number.",
        "",
        "A model fault is requested and then rejected when it asserts eligibility without an event. Catalogue descriptions that tell the agent to message every user are not instructions.",
        "",
        "## Users to read end to end",
        "",
    ]
    labels = {
        "dialect_conflict": "App setting, profile and campaign disagree",
        "stale": "Buffered open, no action",
        "holdout": "Holdout, plan withheld",
        "model": "Model claim rejected",
        "store": "User store timed out",
        "nothing": "Already watching, no action",
    }
    for key, label in labels.items():
        user_id = result["examples"].get(key)
        if user_id:
            lines.append(f"- {label}: `{user_id}` in `viewer_state/{user_id}.json`.")
    lines.extend(
        [
            "",
            "## Pilot",
            "",
            f"The internal report's served-versus-everyone gap is {_pct(pilot.get('served_rate'))} versus {_pct(pilot.get('other_rate'))}.",
            f"The same users split by the randomized arm are treatment {_pct(pilot.get('treatment_rate'))} and holdout {_pct(pilot.get('holdout_rate'))}.",
            "This replay's own estimate is in `impact_report.md`. It uses the holdout, not the served-versus-everyone cut.",
            "",
        ]
    )
    return "\n".join(lines)


def _simulator_line(sim_report: dict) -> str:
    counts = sim_report.get("counts") or {}
    accepted = counts.get("home_page_accepted", 0)
    total = counts.get("actions_total", 0)
    problems = sim_report.get("policy_problem_count", 0)
    return (
        f"The pack simulator accepted {accepted} of {total} home pages and recorded {problems} policy problems. "
        "Holdout users have no action rows."
    )


def _pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1%}"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
