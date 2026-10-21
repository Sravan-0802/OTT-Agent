"""Judge goals from attributable events and compare arms to the holdout."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta

from ott_agent.timing import IST, iso, ist_day, parse_ts, timing

# Last session the pack simulator can emit: replay day + 14 days, hour 22 IST.
HORIZON_END = datetime(2026, 10, 15, 22, 59, tzinfo=IST)


def _true(event: dict) -> datetime | None:
    info = timing(event)
    if not info["trusted"]:
        return None
    return info["true_time"]


def _trial(event: dict) -> bool:
    value = (event.get("properties") or {}).get("is_trial")
    return value is True or str(value).lower() == "true"


def event_meets(goal: dict, event: dict, cat: dict) -> bool:
    """A single trusted event against the goal's stored success rule."""
    if event.get("event") != goal.get("success_event"):
        return False
    when = _true(event)
    start = parse_ts(goal["started_at"])
    deadline = parse_ts(goal["deadline"])
    if when is None or start is None or deadline is None:
        return False
    if not (start < when <= deadline):
        return False
    goal_id = goal["id"]
    props = event.get("properties") or {}
    if "later IST" in (goal.get("success_condition") or ""):
        if not (ist_day(when) > ist_day(start)):
            return False
    if goal_id == "G07":
        title = cat.get(props.get("title_id") or "")
        if title is None or title["slug"] != goal.get("slug"):
            return False
    if goal_id == "G08":
        title = cat.get(props.get("title_id") or "")
        if title is None or title.get("release") is None:
            return False
        release = title["release"]
        began = ist_day(start)
        if not (began - timedelta(days=30) <= release <= began):
            return False
    if goal_id in {"G04", "G05", "G06"} and _trial(event):
        return False
    return True


def judge_goal(goal: dict, events: list[dict], cat: dict, judged_at: datetime) -> dict:
    start = parse_ts(goal["started_at"])
    deadline = parse_ts(goal["deadline"])
    trusted = []
    ambiguous = []
    for event in events:
        if event.get("event") != goal.get("success_event"):
            continue
        received = parse_ts(event.get("received_at"))
        if start is not None and received is not None and received <= start:
            continue
        if event_meets(goal, event, cat):
            trusted.append(event)
            continue
        info = timing(event)
        if not info["trusted"] and received and start and start < received <= (deadline or received):
            ambiguous.append(event)

    if goal["id"] == "G09":
        days = set()
        for event in trusted:
            when = _true(event)
            if when is not None:
                days.add(ist_day(when))
        if len(days) >= 3:
            return _outcome(goal, "succeeded", trusted, judged_at, "Play events fall on 3 distinct IST days inside the window.")
    elif trusted:
        return _outcome(goal, "succeeded", trusted[:3], judged_at, "A trusted event inside the window meets the stored success rule.")

    if deadline is not None and deadline > HORIZON_END:
        return _outcome(
            goal,
            "inconclusive",
            ambiguous,
            judged_at,
            "The deadline is outside the observed 14 days, so a missing success is not a failure.",
        )
    if ambiguous and not trusted:
        return _outcome(
            goal,
            "inconclusive",
            ambiguous,
            judged_at,
            "A matching payload has a phone clock the agent will not trust.",
        )
    return _outcome(goal, "failed", [], judged_at, "The deadline passed with no trusted success event.")


def _outcome(goal: dict, result: str, events: list[dict], judged_at: datetime, notes: str) -> dict:
    return {
        "viewer_id": goal["user_id"],
        "user_id": goal["user_id"],
        "goal_id": goal["id"],
        "decision_id": goal["decision_id"],
        "result": result,
        "signal_refs": [f"event:{event['event_id']}" for event in events if event.get("event_id")],
        "judged_at": iso(judged_at),
        "rule_version": goal.get("rule_version"),
        "notes": notes,
        "holdout": goal.get("arm") == "holdout",
        "attributable": True,
    }


def _rate(successes: int, total: int) -> float | None:
    if total <= 0:
        return None
    return successes / total


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.3f}"


def _se(p1: float | None, n1: int, p2: float | None, n2: int) -> float | None:
    if p1 is None or p2 is None or n1 <= 0 or n2 <= 0:
        return None
    return math.sqrt(max(p1 * (1 - p1), 0) / n1 + max(p2 * (1 - p2), 0) / n2)


def impact_markdown(decisions: list[dict], outcomes: list[dict], pilot: dict, population: int) -> str:
    latest = {}
    for outcome in outcomes:
        latest[outcome["decision_id"]] = outcome
    first = {}
    for decision in decisions:
        if not decision.get("measurement_cohort") or decision.get("status") == "DUPLICATE":
            continue
        if not decision.get("goal"):
            continue
        first.setdefault(decision["viewer_id"], decision)

    by_goal = defaultdict(list)
    for decision in first.values():
        by_goal[decision["goal"]["id"]].append(decision)

    lines = [
        "# Impact report",
        "",
        "The assignment is randomized. `replay/arms.csv` puts each user in `agent` or `holdout` before any replay outcome exists. Holdout users receive no action. The primary comparison is those two arms, inside the same goal, after the deadline.",
        "",
        "A user enters the cohort only when this same policy selects a goal. Stale opens, store timeouts and users with nothing to solve stay out. Inconclusive judgments stay out of the rates.",
        "",
        "## Primary estimate",
        "",
    ]
    if not by_goal:
        lines.append("No measurement cohort was recorded.")
        lines.append("")
    pooled = {"agent": [0, 0], "holdout": [0, 0]}
    for goal_id in sorted(by_goal):
        arms = {"agent": [0, 0, 0], "holdout": [0, 0, 0]}  # success, judged, censored
        for decision in by_goal[goal_id]:
            arm = "holdout" if decision.get("arm") == "holdout" or decision.get("status") == "HOLDOUT" else "agent"
            outcome = latest.get(decision["decision_id"])
            if outcome is None or outcome.get("result") == "inconclusive":
                arms[arm][2] += 1
                continue
            arms[arm][1] += 1
            if outcome.get("result") == "succeeded":
                arms[arm][0] += 1
        agent_p = _rate(arms["agent"][0], arms["agent"][1])
        hold_p = _rate(arms["holdout"][0], arms["holdout"][1])
        diff = None if agent_p is None or hold_p is None else agent_p - hold_p
        se = _se(agent_p, arms["agent"][1], hold_p, arms["holdout"][1])
        pooled["agent"][0] += arms["agent"][0]
        pooled["agent"][1] += arms["agent"][1]
        pooled["holdout"][0] += arms["holdout"][0]
        pooled["holdout"][1] += arms["holdout"][1]
        lines.append(f"### `{goal_id}`")
        lines.append("")
        lines.append(f"- Agent arm: {arms['agent'][0]} / {arms['agent'][1]} ({_fmt(agent_p)}), {arms['agent'][2]} censored")
        lines.append(f"- Holdout: {arms['holdout'][0]} / {arms['holdout'][1]} ({_fmt(hold_p)}), {arms['holdout'][2]} censored")
        lines.append(f"- Difference: {_fmt(diff)}")
        if se is not None and diff is not None:
            lines.append(f"- Approximate standard error: {se:.3f}.")
            if abs(diff) < 2 * se:
                lines.append("- This gap is inside the noise of this sample. It is not a win.")
        lines.append("")

    agent_p = _rate(*pooled["agent"])
    hold_p = _rate(*pooled["holdout"])
    lines.extend(
        [
            "### Pooled across goals",
            "",
            f"- Agent arm: {pooled['agent'][0]} / {pooled['agent'][1]} ({_fmt(agent_p)})",
            f"- Holdout: {pooled['holdout'][0]} / {pooled['holdout'][1]} ({_fmt(hold_p)})",
            "",
            "Pooling mixes goals with different base rates. Use it only as a summary. The per-goal table is the estimate.",
            "",
            "## Why served-versus-everyone misleads",
            "",
            "People who receive a home page were online, eligible, and in a dialect that had enough titles. Everyone else includes the holdout, users the agent refused to touch, users who never opened the app, and users who had already finished the goal. That gap is a selection effect.",
            "",
            "Inside this replay the served cut matches the arm split: a cohort user was served only when the arm was agent. That agreement is not an impact estimate. The pilot below is the cut that goes wrong.",
            "",
        ]
    )
    served_ids = set()
    served_success = 0
    for decision in decisions:
        if decision.get("status") == "PUBLISHED" and decision.get("effects_applied"):
            served_ids.add(decision["viewer_id"])
    served_judged = 0
    other_success = set()
    other_ids = set()
    for decision in first.values():
        outcome = latest.get(decision["decision_id"])
        if outcome is None or outcome.get("result") not in {"succeeded", "failed"}:
            continue
        if decision["viewer_id"] in served_ids:
            served_judged += 1
            if outcome["result"] == "succeeded":
                served_success += 1
        else:
            other_ids.add(decision["viewer_id"])
            if outcome["result"] == "succeeded":
                other_success.add(decision["viewer_id"])
    lines.append(
        f"- Served and judged: {_fmt(_rate(served_success, served_judged))} ({served_success} / {served_judged})."
    )
    lines.append(
        f"- Cohort users who were not served: {_fmt(_rate(len(other_success), len(other_ids)))} ({len(other_success)} / {len(other_ids)})."
    )
    lines.append("")
    lines.append("The pilot write-up makes the same mistake on a larger scale. It is reproduced here from the pilot files, not used as this run's result.")
    lines.append("")
    if pilot:
        lines.append(f"- Pilot headline: {pilot['headline']}")
        lines.append(f"- Pilot served rate: {_fmt(pilot['served_rate'])} ({pilot['served_n']} users).")
        lines.append(f"- Pilot everyone-else rate: {_fmt(pilot['other_rate'])} ({pilot['other_n']} users).")
        lines.append(f"- Pilot treatment arm: {_fmt(pilot['treatment_rate'])} ({pilot['treatment_n']} users).")
        lines.append(f"- Pilot holdout arm: {_fmt(pilot['holdout_rate'])} ({pilot['holdout_n']} users).")
        lines.append("")
        lines.append("The holdout gap is the comparison that matches the assignment. The headline gap does not.")
        lines.append("")
    lines.append(f"Triggered population in this replay: {population} users.")
    lines.append("")
    return "\n".join(lines)


def pilot_table(assignment_rows: list[dict], outcome_rows: list[dict]) -> dict:
    outcomes = {row["user_id"]: row for row in outcome_rows}
    groups = {"served": [], "other": [], "treatment": [], "holdout": []}
    for row in assignment_rows:
        outcome = outcomes.get(row["user_id"])
        if outcome is None:
            continue
        success = outcome.get("succeeded") == "true"
        if row.get("served") == "true":
            groups["served"].append(success)
        else:
            groups["other"].append(success)
        if row.get("arm") == "treatment":
            groups["treatment"].append(success)
        elif row.get("arm") == "holdout":
            groups["holdout"].append(success)

    def rate(values: list[bool]) -> tuple[float | None, int]:
        if not values:
            return None, 0
        return sum(values) / len(values), len(values)

    served_rate, served_n = rate(groups["served"])
    other_rate, other_n = rate(groups["other"])
    treatment_rate, treatment_n = rate(groups["treatment"])
    holdout_rate, holdout_n = rate(groups["holdout"])
    return {
        "headline": "Served users reached their goal 40.1% of the time against 17.7% for everyone else.",
        "served_rate": served_rate,
        "served_n": served_n,
        "other_rate": other_rate,
        "other_n": other_n,
        "treatment_rate": treatment_rate,
        "treatment_n": treatment_n,
        "holdout_rate": holdout_rate,
        "holdout_n": holdout_n,
    }
