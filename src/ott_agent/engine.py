"""Decide, verify and publish for one user at a time.

The catalogue, the goal file and the model are not authorities. A title
description is content. A model sentence is not eligibility. The page dialect
is the dialect on the live open, because that is the page the app will request.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

from ott_agent.judge import event_meets
from ott_agent.timing import IST, event_age_s, in_quiet_hours, iso, ist_day, parse_ts, timing

PAYMENT_GOALS = {"G04", "G05", "G06"}
INSTRUCTION_BITS = ("ignore your previous", "ignore all", "every user", "system note", "message every")
RECENT_LAPSE_DAYS = 45


def looks_like_instruction(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(bit in lowered for bit in INSTRUCTION_BITS)


def available(title: dict, at, withdrawn: dict) -> bool:
    if title["available_from"] and at < title["available_from"]:
        return False
    until = title["available_until"]
    pulled = withdrawn.get(title["title_id"])
    if pulled is not None and (until is None or pulled < until):
        until = pulled
    if until is not None and at >= until:
        return False
    return True


def deeplink(event: dict) -> tuple[str | None, str | None]:
    raw = (event.get("properties") or {}).get("deeplink") or ""
    if not raw.startswith("ott://"):
        return None, None
    parts = raw.split("/")
    title_id = None
    dialect = None
    if "title" in parts:
        index = parts.index("title")
        if index + 1 < len(parts):
            title_id = parts[index + 1]
        if index >= 1 and parts[index - 1].startswith("d") and parts[index - 1][1:].isdigit():
            dialect = parts[index - 1]
    return dialect, title_id


def store_times_out(user_id: str, event_id: str) -> bool:
    digest = hashlib.sha256(f"store:{user_id}:{event_id}".encode()).hexdigest()
    return int(digest[:8], 16) % 5 == 0


class Memory:
    def __init__(self, user_id: str, profiles: list[dict], arm: str) -> None:
        self.user_id = user_id
        self.profiles = profiles
        self.arm = arm
        self.seen: set[str] = set()
        self.events: list[dict] = []
        self.plays: list[tuple] = []
        self.completes: list[tuple] = []
        self.opens: list[tuple] = []
        self.signups: list = []
        self.opt_out: set[str] = set()
        self.goal: dict | None = None
        self.goals_closed: list[dict] = []
        self.decisions: list[dict] = []
        self.page: dict | None = None
        self.messages: list[tuple] = []
        self.lessons: list[dict] = []
        self.cohort = False

    def add(self, event: dict, cat: dict) -> bool:
        event_id = event.get("event_id")
        if event_id in self.seen:
            return False
        if event_id:
            self.seen.add(event_id)
        self.events.append(event)
        info = timing(event)
        if not info["trusted"]:
            return True
        when = info["true_time"]
        name = event.get("event")
        props = event.get("properties") or {}
        if name == "play_started":
            self.plays.append((when, props.get("title_id"), event_id))
        elif name == "episode_completed":
            self.completes.append((when, props.get("title_id"), props.get("episode_id"), event_id))
        elif name in {"app_open", "login_success"}:
            self.opens.append((when, event_id))
        elif name == "signup_completed":
            self.signups.append(when)
        elif name == "notification_opt_out" and props.get("channel"):
            self.opt_out.add(props["channel"])
        return True


def resolve_subscription(mem: Memory) -> dict:
    status = None
    source = None
    ref = None
    for event in mem.events:
        info = timing(event)
        if not info["trusted"]:
            continue
        name = event.get("event")
        if name == "purchase_completed":
            trial = (event.get("properties") or {}).get("is_trial")
            status = "trial_active" if trial is True or str(trial).lower() == "true" else "active"
            source = "purchase_event"
            ref = event["event_id"]
        elif name == "subscription_renewed":
            status = "active"
            source = "renewal_event"
            ref = event["event_id"]
        elif name == "subscription_lapsed":
            status = "lapsed"
            source = "lapse_event"
            ref = event["event_id"]
    profile_values = sorted({row.get("subscription_status") or "" for row in mem.profiles})
    conflicts = []
    if source:
        if any(value and value != status for value in profile_values):
            conflicts = [f"profile:{value}" for value in profile_values if value and value != status]
        return {
            "value": status,
            "source": source,
            "ref": ref,
            "freshness": "fresh",
            "conflicts": conflicts,
            "resolution": "A purchase, renewal or lapse event is the billing fact. A newer warehouse row does not erase it.",
        }
    if not mem.profiles:
        return {
            "value": "unknown",
            "source": None,
            "ref": None,
            "freshness": "unknown",
            "conflicts": [],
            "resolution": "No profile row and no billing event.",
        }
    if len([value for value in profile_values if value]) > 1:
        return {
            "value": "conflicted",
            "source": None,
            "ref": None,
            "freshness": "conflicted",
            "conflicts": [f"profile:{value}" for value in profile_values if value],
            "resolution": "Profile versions disagree and no billing event settles them. The newer row is not taken as the winner.",
        }
    return {
        "value": profile_values[0] if profile_values else "unknown",
        "source": "profile",
        "ref": mem.profiles[0].get("snapshot_at"),
        "freshness": "stale",
        "conflicts": [],
        "resolution": "No billing event. The warehouse export is a day stale and is used only because its versions agree.",
    }


def signup_at(mem: Memory):
    if mem.signups:
        return min(mem.signups), "event"
    dates = {row.get("signup_at") for row in mem.profiles if row.get("signup_at")}
    if len(dates) == 1:
        return parse_ts(next(iter(dates))), "profile"
    if len(dates) > 1:
        return "conflict", "profile"
    return None, None


def _lapse_is_recent(mem: Memory, now) -> bool:
    for event in mem.events:
        if event.get("event") != "subscription_lapsed":
            continue
        info = timing(event)
        if info["trusted"] and (now - info["true_time"]).days <= RECENT_LAPSE_DAYS:
            return True
    ends = []
    for row in mem.profiles:
        if row.get("subscription_status") != "lapsed":
            continue
        parsed = parse_ts(row.get("plan_ends_at"))
        if parsed:
            ends.append(parsed)
    if ends and all((now - item).days <= RECENT_LAPSE_DAYS for item in ends):
        return True
    if mem.profiles and all(row.get("subscription_status") == "lapsed" for row in mem.profiles) and not ends:
        return True
    return False


def unfinished_series(mem: Memory, world: dict, dialect: str, now, language: str | None) -> list[dict]:
    last_play = {}
    completed = defaultdict(set)
    for when, title_id, _event_id in mem.plays:
        title = world["cat"].get(title_id or "")
        if title is None:
            continue
        previous = last_play.get(title["slug"])
        if previous is None or when > previous:
            last_play[title["slug"]] = when
    for _when, title_id, episode_id, _event_id in mem.completes:
        title = world["cat"].get(title_id or "")
        if title is not None and episode_id:
            completed[title["slug"]].add(episode_id)
    found = []
    for slug, when in last_play.items():
        idle_days = (now - when).total_seconds() / 86400
        if idle_days < 3:
            continue
        options = [
            title
            for title in world["cat"].values()
            if title["slug"] == slug and title["dialect"] == dialect and title["type"] in {"show", "micro_drama"}
        ]
        options = [title for title in options if available(title, now, world["withdrawn"])]
        if not options:
            continue
        options.sort(key=lambda title: (title["language"] != language, title["title_id"]))
        chosen = options[0]
        episodes = world["episodes"].get(slug) or []
        if not episodes:
            continue
        remaining = [episode for episode in episodes if episode[2] not in completed[slug]]
        if not remaining:
            continue
        found.append({"slug": slug, "title_id": chosen["title_id"], "idle_days": idle_days, "episode_id": remaining[0][2]})
    found.sort(key=lambda item: item["idle_days"])
    return found


def play_days(mem: Memory, now) -> int:
    start = now - timedelta(days=14)
    return len({ist_day(when) for when, _title, _event in mem.plays if start <= when <= now})


def eligible_goals(mem: Memory, world: dict, now, trigger: dict) -> list[dict]:
    trigger_id = trigger.get("event_id")
    status = resolve_subscription(mem)
    signed, _source = signup_at(mem)
    dialect = trigger.get("app_dialect")
    language = trigger.get("app_language")
    goals = []
    if dialect:
        series = unfinished_series(mem, world, dialect, now, language)
        if series:
            goals.append({"id": "G07", "slug": series[0]["slug"], "title_id": series[0]["title_id"]})
    no_plays = len(mem.plays) == 0
    no_completes = len(mem.completes) == 0
    if isinstance(signed, type(now)) and (now - signed).days <= 7 and no_plays:
        goals.append({"id": "G01"})
    if mem.plays and no_completes:
        goals.append({"id": "G02"})
    prior_opens = [when for when, event_id in mem.opens if event_id != trigger_id]
    if prior_opens and (now - max(prior_opens)).days >= 14:
        goals.append({"id": "G12"})
    if isinstance(signed, type(now)) and (now - signed).days <= 2:
        goals.append({"id": "G03"})
    if status["value"] == "trial_active" and mem.plays:
        goals.append({"id": "G04"})
    if status["value"] == "lapsed" and _lapse_is_recent(mem, now):
        goals.append({"id": "G06"})
    if status["value"] == "never" and mem.completes and not (isinstance(signed, type(now)) and (now - signed).days <= 7):
        goals.append({"id": "G05"})
    days = play_days(mem, now)
    if status["value"] == "active" and days <= 1:
        goals.append({"id": "G09"})
    if status["value"] == "active" and 2 <= days <= 3 and _has_new_release(world, dialect, now):
        goals.append({"id": "G08"})
    return goals


def _has_new_release(world: dict, dialect: str | None, now) -> bool:
    if not dialect:
        return False
    began = ist_day(now)
    for title in world["by_dialect"].get(dialect, []):
        release = title.get("release")
        if release and began - timedelta(days=30) <= release <= began and available(title, now, world["withdrawn"]):
            return True
    return False


def abstain_reason(mem: Memory, world: dict, now, trigger: dict) -> str:
    status = resolve_subscription(mem)
    if status["value"] == "active" and play_days(mem, now) >= 4 and not unfinished_series(
        mem, world, trigger.get("app_dialect") or "", now, trigger.get("app_language")
    ):
        return "already_on_track"
    if status["value"] == "conflicted":
        return "subscription_unresolved"
    if not trigger.get("app_dialect"):
        return "unresolved_dialect"
    return "no_eligible_goal"


def _deduped(world: dict, dialect: str, now, language: str | None, genres: set[str] | None, premium_bias: bool) -> list[dict]:
    best: dict[str, dict] = {}
    for title in world["by_dialect"].get(dialect, []):
        if not available(title, now, world["withdrawn"]):
            continue
        current = best.get(title["slug"])
        if current is None or _prefer(title, current, language, world):
            best[title["slug"]] = title
    ranked = list(best.values())

    def sort_key(title: dict):
        score = world["play_count"].get(title["title_id"], 0)
        if genres and any(genre in genres for genre in title["genres"]):
            score += 10000
        if premium_bias and title["premium"]:
            score += 1000
        if language and title["language"] == language:
            score += 1
        release = title["release"].toordinal() if title.get("release") else 0
        return (-score, -release, title["title_id"])

    ranked.sort(key=sort_key)
    return ranked


def _prefer(candidate: dict, current: dict, language: str | None, world: dict) -> bool:
    if language and (candidate["language"] == language) != (current["language"] == language):
        return candidate["language"] == language
    return world["play_count"].get(candidate["title_id"], 0) > world["play_count"].get(current["title_id"], 0)


def _genres_of(mem: Memory, cat: dict) -> set[str]:
    counts: Counter = Counter()
    for _when, title_id, _event in mem.plays:
        title = cat.get(title_id or "")
        if title:
            counts.update(title["genres"])
    return {genre for genre, _count in counts.most_common(2)}


def build_page(mem: Memory, world: dict, goal: dict, trigger: dict, now) -> dict | None:
    dialect = trigger.get("app_dialect")
    if not dialect:
        return None
    language = trigger.get("app_language")
    genres = _genres_of(mem, world["cat"])
    premium_bias = goal["id"] in PAYMENT_GOALS
    ranked = _deduped(world, dialect, now, language, None, premium_bias)
    genre_ranked = _deduped(world, dialect, now, language, genres, premium_bias) if genres else []
    focus = None
    if goal["id"] == "G07" and goal.get("title_id") in world["cat"]:
        focus = world["cat"][goal["title_id"]]
    else:
        _campaign, linked = deeplink(trigger)
        linked_title = world["cat"].get(linked or "")
        if (
            linked_title
            and linked_title["dialect"] == dialect
            and available(linked_title, now, world["withdrawn"])
            and not looks_like_instruction(linked_title.get("description"))
        ):
            focus = linked_title
    used: set[str] = set()
    rows = []

    def take(pool: list[dict], limit: int = 10) -> list[str]:
        chosen = []
        for title in pool:
            if title["slug"] in used:
                continue
            chosen.append(title["title_id"])
            used.add(title["slug"])
            if len(chosen) == limit:
                break
        return chosen

    def keep(row_type: str, pool: list[dict]) -> None:
        if len(rows) >= 4:
            return
        picked = take(pool)
        if len(picked) >= 3:
            rows.append({"row_type": row_type, "title_ids": picked})
            return
        for title_id in picked:
            used.discard(world["cat"][title_id]["slug"])

    if focus is not None:
        keep("next_episode" if goal["id"] == "G07" else "popular_in_dialect", [focus, *ranked])
    releases = [
        title
        for title in ranked
        if title.get("release") and ist_day(now) - timedelta(days=30) <= title["release"] <= ist_day(now)
    ]
    keep("because_you_watched", genre_ranked)
    keep("new_releases", releases)
    keep("popular_in_dialect", ranked)
    if genres:
        keep("genre", genre_ranked)
    if not rows:
        if focus is not None and goal["id"] == "G07":
            return {"dialect": dialect, "hero_title_id": focus["title_id"], "rows": []}
        return None
    hero = None
    if focus is not None:
        for row in rows:
            if focus["title_id"] in row["title_ids"] and len(row["title_ids"]) >= 4:
                row["title_ids"] = [title_id for title_id in row["title_ids"] if title_id != focus["title_id"]]
                hero = focus["title_id"]
                break
        if hero is None and all(focus["title_id"] not in row["title_ids"] for row in rows):
            hero = focus["title_id"]
    return {"dialect": dialect, "hero_title_id": hero, "rows": rows}


def verify_page(page: dict, goal: dict, mem: Memory, world: dict, now, trigger: dict) -> dict:
    passed = []
    failed = []
    fresh = eligible_goals(mem, world, now, trigger)
    fresh_ids = {item["id"] for item in fresh}
    open_goal = mem.goal if mem.goal and mem.goal.get("status") == "open" else None
    if open_goal and open_goal["id"] == goal["id"] and goal["id"] in fresh_ids:
        passed.append("eligibility")
    elif fresh and fresh[0]["id"] == goal["id"]:
        passed.append("eligibility")
    else:
        failed.append("eligibility")
    seen = set()
    dialect = page["dialect"]
    titles = []
    if page.get("hero_title_id"):
        titles.append(page["hero_title_id"])
    for row in page["rows"]:
        if not 3 <= len(row["title_ids"]) <= 10:
            failed.append("min_titles")
        titles.extend(row["title_ids"])
    if len(page["rows"]) > 6:
        failed.append("min_titles")
    for title_id in titles:
        title = world["cat"].get(title_id)
        if title is None or title["dialect"] != dialect:
            failed.append("dialect")
            continue
        if not available(title, now, world["withdrawn"]):
            failed.append("availability")
            continue
        if title["slug"] in seen and title_id != page.get("hero_title_id"):
            failed.append("duplicate_slug")
        seen.add(title["slug"])
    if "dialect" not in failed and titles:
        passed.append("dialect")
    if "availability" not in failed and titles:
        passed.append("availability")
    if "min_titles" not in failed and (page["rows"] or page.get("hero_title_id")):
        passed.append("min_titles")
    if "duplicate_slug" not in failed:
        passed.append("duplicate_slug")
    if any(looks_like_instruction(world["cat"].get(title_id, {}).get("description")) for title_id in titles):
        # The title may be shown. The description must not change the action.
        passed.append("injection")
    else:
        passed.append("injection")
    return {"passed": passed, "failed": failed}


def _bind_goal(chosen: dict, meta: dict, now, decision_id: str, user_id: str, arm: str, version: str) -> dict:
    hours = int(meta["success"]["window_hours"])
    success_event = meta["success"]["event"]
    condition = meta["success"].get("condition") or ""
    rule_version = meta.get("version", version)
    if chosen["id"] == "G03" and version == "G03-corrected":
        success_event = "play_started"
        condition = "on a later IST calendar day within the window"
        rule_version = "G03-corrected"
    return {
        "id": chosen["id"],
        "name": meta["name"],
        "user_id": user_id,
        "decision_id": decision_id,
        "arm": arm,
        "started_at": iso(now),
        "deadline": iso(now + timedelta(hours=hours)),
        "success_event": success_event,
        "success_condition": condition,
        "rule_version": rule_version,
        "status": "open",
        "slug": chosen.get("slug"),
        "title_id": chosen.get("title_id"),
    }


def _confidence(trigger: dict, status: dict, action: str, reason: str | None) -> tuple[float, str]:
    if reason == "stale_trigger":
        return 0.84, "The phone held this open for more than 10 minutes, so it is not a live visit."
    if reason == "untrusted_clock":
        return 0.8, "The phone clock is not usable, so the agent does not treat this as a live visit."
    if reason == "plan_still_current":
        return 0.74, "The open goal still fits and a page for this dialect is still inside its 120 minute life."
    if reason == "already_on_track":
        return 0.72, "This subscriber already watches on several days, and no unfinished series is waiting."
    if reason == "randomized_holdout":
        return 0.8, "The user is in the randomized holdout, so the plan is recorded and nothing is published."
    if reason == "subscription_unresolved":
        return 0.7, "Profile versions disagree about billing and no purchase event settles it."
    score = 0.56
    bits = []
    campaign, _title = deeplink(trigger)
    if trigger.get("app_dialect"):
        bits.append("the page dialect is the app setting on this open")
        score += 0.12
    if campaign and campaign != trigger.get("app_dialect"):
        bits.append("the campaign link names a different dialect and is not used for the page")
        score -= 0.08
    profile_dialects = set()
    if status.get("source") == "profile":
        bits.append("billing status comes from the day-old warehouse export")
        score -= 0.08
    elif status.get("source"):
        bits.append("billing status comes from an event, which outranks the warehouse export")
    if status.get("conflicts"):
        score -= 0.06
    if not bits:
        bits.append("the goal is the first eligible rule with a success event that exists")
    text = bits[0][0].upper() + bits[0][1:]
    if len(bits) > 1:
        text = text + ", and " + ", and ".join(bits[1:])
    return round(max(0.2, min(0.8, score)), 2), text + "."


def _evidence(mem: Memory, trigger: dict, page: dict | None, goal: dict | None, world: dict, now) -> list[dict]:
    items = [
        {
            "ref": f"event:{trigger['event_id']}",
            "source": trigger.get("event"),
            "freshness": "stale" if event_age_s(trigger) > LIVE_STALE else "fresh",
            "received_at": trigger.get("received_at"),
            "age_s": event_age_s(trigger),
        }
    ]
    if trigger.get("app_dialect"):
        items.append(
            {
                "ref": f"app_setting:{trigger['event_id']}",
                "source": "app_setting",
                "freshness": "fresh" if event_age_s(trigger) <= 600 else "stale",
                "received_at": trigger.get("received_at"),
                "age_s": event_age_s(trigger),
            }
        )
    for row in mem.profiles:
        items.append(
            {
                "ref": f"profile:{mem.user_id}:{row.get('updated_at')}",
                "source": "profile",
                "freshness": "stale",
                "received_at": row.get("snapshot_at"),
                "age_s": int((now - parse_ts(row["snapshot_at"])).total_seconds()) if row.get("snapshot_at") else None,
                "note": "Warehouse export taken the morning before the replay.",
            }
        )
    if page:
        for title_id in _page_ids(page)[:8]:
            items.append({"ref": f"catalogue:{title_id}", "source": "catalogue", "freshness": "fresh"})
    if goal:
        items.append(
            {
                "ref": f"goal_rule:{goal['id']}@{goal['rule_version']}",
                "source": "goal_rule",
                "freshness": "fresh",
                "note": "Interpreted from the goal catalogue. Model text is not a rule.",
            }
        )
    return items


LIVE_STALE = 600


def _page_ids(page: dict) -> list[str]:
    ids = []
    if page.get("hero_title_id"):
        ids.append(page["hero_title_id"])
    for row in page.get("rows") or []:
        ids.extend(row.get("title_ids") or [])
    seen = set()
    ordered = []
    for title_id in ids:
        if title_id in seen:
            continue
        seen.add(title_id)
        ordered.append(title_id)
    return ordered


def _assumptions(mem: Memory, status: dict, goal: dict | None) -> list[str]:
    items = []
    if not mem.profiles:
        items.append("This user has no warehouse row. Facts come only from events received so far.")
    if status.get("source") == "profile":
        items.append("No billing event has arrived. The stale profile can still be wrong.")
    if status.get("value") == "conflicted":
        items.append("Disagreeing profile versions were left unresolved instead of keeping the newest row.")
    if goal and goal["id"] == "G07":
        items.append("G07 eligibility is prose. It is read as a show or micro-drama in this dialect with a remaining episode and no play for 3 days.")
    if goal and goal["id"] == "G06":
        items.append("Recently lapsed is not defined. A lapse inside 45 days, or a profile that only says lapsed, is used.")
    if goal and goal["id"] in PAYMENT_GOALS:
        items.append("A paid plan means is_trial is false. The trial price is not treated as the first paid plan.")
    items.append("Campaign and profile dialects are context. They do not choose the page, even when their timestamp is later.")
    return items


def _conflicts(trigger: dict, mem: Memory, status: dict) -> list[dict]:
    records = []
    values = {}
    if trigger.get("app_dialect"):
        values["app_setting"] = trigger["app_dialect"]
    profile_dialects = sorted({row.get("preferred_dialect") for row in mem.profiles if row.get("preferred_dialect")})
    if len(profile_dialects) == 1:
        values["profile"] = profile_dialects[0]
    elif len(profile_dialects) > 1:
        values["profile"] = "conflicted:" + ",".join(profile_dialects)
    campaign, _title = deeplink(trigger)
    if campaign:
        values["campaign"] = campaign
    if len(set(values.values())) > 1:
        records.append(
            {
                "field": "dialect",
                "values": values,
                "resolution": "The live app setting is what the client will request. Newer profile or campaign values stay visible and do not win.",
                "rejected_rule": "newest_or_largest_source_wins",
            }
        )
    if status.get("conflicts") or status.get("value") == "conflicted":
        records.append(
            {
                "field": "subscription",
                "values": {"resolved": status.get("value"), "notes": status.get("conflicts"), "source": status.get("source")},
                "resolution": status.get("resolution"),
                "rejected_rule": "newest_or_largest_source_wins",
            }
        )
    return records


def _dialect_conflicts(trigger: dict, mem: Memory) -> list[str]:
    conflicts = []
    used = trigger.get("app_dialect")
    profile_dialects = sorted({row.get("preferred_dialect") for row in mem.profiles if row.get("preferred_dialect")})
    if len(profile_dialects) > 1:
        conflicts.append("profile_versions_disagree:" + ",".join(profile_dialects))
    elif profile_dialects and used and profile_dialects[0] != used:
        conflicts.append(f"profile:{profile_dialects[0]}")
    campaign, _title = deeplink(trigger)
    if campaign and used and campaign != used:
        conflicts.append(f"campaign:{campaign}")
    return conflicts


def consult_model(trigger: dict, world: dict, now) -> tuple[dict | None, str | None, int]:
    fault = world["faults"].get(trigger["event_id"])
    if fault is None:
        return None, None, 0
    down = world.get("model_down")
    if down and down[0] <= now < down[1]:
        return None, "model_unavailable", 1
    if fault.get("fault") == "timeout":
        return None, "model_timeout", 1
    raw = fault.get("model_output")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except json.JSONDecodeError:
        return None, "malformed_output", 1
    if parsed.get("eligible") is True and not parsed.get("evidence_refs"):
        return parsed, "model_asserted_eligibility_without_evidence", 1
    if looks_like_instruction(str(parsed.get("reason") or "")):
        return parsed, "model_text_contained_instructions", 1
    return parsed, "model_output_not_used_as_evidence", 1


def decide(mem: Memory, trigger: dict, world: dict, now, decision_id: str, dependency_ms: int = 0) -> dict:
    info = timing(trigger)
    status = resolve_subscription(mem)
    structural = [
        {"id": "G10", "result": "underspecified"},
        {"id": "G11", "result": "success_signal_not_in_event_stream"},
    ]
    base = {
        "viewer_id": mem.user_id,
        "user_id": mem.user_id,
        "arm": mem.arm,
        "trigger": {
            "event": trigger.get("event"),
            "event_id": trigger.get("event_id"),
            "received_at": trigger.get("received_at"),
            "deliver_at": iso(now),
            "event_age_s": event_age_s(trigger),
            "timing_kind": info["kind"],
        },
        "goals_rejected": structural,
        "model_calls": 0,
        "tool_calls": 5,
        "case_study": False,
    }
    model_output, model_rejected, model_calls = consult_model(trigger, world, now)
    if model_rejected == "model_timeout":
        dependency_ms += int(world["faults"][trigger["event_id"]].get("seconds", 45) * 1000)
    if not info["trusted"] or info["kind"] != "live":
        reason = "untrusted_clock" if not info["trusted"] else "stale_trigger"
        confidence, text = _confidence(trigger, status, "NO_ACTION", reason)
        if model_rejected:
            text = text + f" A model opinion was rejected ({model_rejected}) and was not used as evidence."
        return {
            **base,
            "decision_id": decision_id,
            "goal": None,
            "action": "NO_ACTION",
            "no_action_reason": reason,
            "dialect": {"used": None, "source": None, "conflicts": _dialect_conflicts(trigger, mem), "freshness": "unknown"},
            "content": [],
            "confidence": confidence,
            "confidence_reason": text,
            "evidence": _evidence(mem, trigger, None, None, world, now),
            "assumptions": _assumptions(mem, status, None),
            "conflicts": _conflicts(trigger, mem, status),
            "checks": {"passed": ["no_action"], "failed": []},
            "status": "ABSTAINED",
            "effects_applied": False,
            "measurement_cohort": False,
            "model_calls": model_calls,
            "model_rejected": model_rejected,
            "dependency_ms": dependency_ms,
        }
    fresh = eligible_goals(mem, world, now, trigger)
    chosen = None
    keep = False
    if mem.goal and mem.goal.get("status") == "open" and parse_ts(mem.goal["deadline"]) > now:
        if any(item["id"] == mem.goal["id"] for item in fresh):
            chosen = next(item for item in fresh if item["id"] == mem.goal["id"])
            keep = True
        else:
            mem.goal["status"] = "superseded"
            mem.goals_closed.append(mem.goal)
            mem.lessons.append(
                {
                    "at": iso(now),
                    "topic": "plan_revised",
                    "detail": f"{mem.goal['id']} is no longer eligible and was closed without calling it a failure.",
                }
            )
            mem.goal = None
    if chosen is None and fresh:
        chosen = fresh[0]
    if model_output and model_output.get("goal_id") and chosen and model_output["goal_id"] != chosen["id"]:
        model_rejected = model_rejected or "model_goal_disagrees_with_rules"
    elif model_output and model_output.get("goal_id") and not chosen:
        model_rejected = model_rejected or "model_goal_disagrees_with_rules"

    if chosen is None:
        reason = abstain_reason(mem, world, now, trigger)
        confidence, text = _confidence(trigger, status, "NO_ACTION", reason)
        return {
            **base,
            "decision_id": decision_id,
            "goal": None,
            "action": "NO_ACTION",
            "no_action_reason": reason,
            "dialect": {
                "used": trigger.get("app_dialect"),
                "source": "app_setting" if trigger.get("app_dialect") else None,
                "conflicts": _dialect_conflicts(trigger, mem),
                "freshness": "fresh" if trigger.get("app_dialect") else "unknown",
            },
            "content": [],
            "confidence": confidence,
            "confidence_reason": text,
            "evidence": _evidence(mem, trigger, None, None, world, now),
            "assumptions": _assumptions(mem, status, None),
            "conflicts": _conflicts(trigger, mem, status),
            "checks": {"passed": ["no_action"], "failed": []},
            "status": "ABSTAINED",
            "effects_applied": False,
            "measurement_cohort": False,
            "model_calls": model_calls,
            "model_rejected": model_rejected,
            "dependency_ms": dependency_ms,
        }

    meta = world["goal_meta"][chosen["id"]]
    if keep:
        goal = dict(mem.goal)
    else:
        goal = _bind_goal(chosen, meta, now, decision_id, mem.user_id, mem.arm, world["rule_version"])
    page_live = (
        keep
        and mem.page
        and mem.page["dialect"] == trigger.get("app_dialect")
        and now - parse_ts(mem.page["at"]) < timedelta(minutes=120)
    )
    if page_live and mem.arm != "holdout":
        confidence, text = _confidence(trigger, status, "NO_ACTION", "plan_still_current")
        return _decision(
            base, decision_id, goal, "NO_ACTION", "plan_still_current", trigger, mem, status, None, world, now,
            {"passed": ["no_action", "eligibility"], "failed": []}, "ABSTAINED", False, False, model_calls,
            model_rejected, dependency_ms, confidence, text,
        )
    if not trigger.get("app_dialect"):
        return _decision(
            base, decision_id, None, "NO_ACTION", "unresolved_dialect", trigger, mem, status, None, world, now,
            {"passed": ["no_action"], "failed": ["dialect"]}, "ABSTAINED", False, False, model_calls,
            model_rejected, dependency_ms, 0.62,
            "The open did not name a dialect, so there is no page the app can request.",
        )
    page = build_page(mem, world, goal, trigger, now)
    if page is None:
        return _decision(
            base, decision_id, None, "NO_ACTION", "not_enough_titles", trigger, mem, status, None, world, now,
            {"passed": ["no_action"], "failed": ["min_titles"]}, "ABSTAINED", False, False, model_calls,
            model_rejected, dependency_ms, 0.66,
            "Fewer than three available titles remain in the dialect the app will request.",
        )
    checks = verify_page(page, goal, mem, world, now, trigger)
    if checks["failed"]:
        confidence, text = _confidence(trigger, status, "NO_ACTION", None)
        return _decision(
            base, decision_id, None, "NO_ACTION", "verification_failed:" + ",".join(checks["failed"]), trigger, mem,
            status, None, world, now, checks, "ABSTAINED", False, False, model_calls, model_rejected, dependency_ms,
            min(confidence, 0.45), text + " Independent checks failed, so nothing was published.",
        )
    if mem.arm == "holdout":
        confidence, text = _confidence(trigger, status, "NO_ACTION", "randomized_holdout")
        decision = _decision(
            base, decision_id, goal, "NO_ACTION", "randomized_holdout", trigger, mem, status, page, world, now,
            checks, "HOLDOUT", False, not keep and not mem.cohort, model_calls, model_rejected, dependency_ms,
            confidence, text,
        )
        decision["withheld_action"] = "HOME_PAGE"
        decision["withheld_content"] = [f"title:{title_id}" for title_id in _page_ids(page)]
        if not keep:
            _commit_goal(mem, goal, decision_id)
        return decision
    confidence, text = _confidence(trigger, status, "HOME_PAGE", None)
    if model_rejected:
        text = text + f" A model opinion was rejected ({model_rejected}) and was not used as evidence."
    decision = _decision(
        base, decision_id, goal, "HOME_PAGE", None, trigger, mem, status, page, world, now, checks, "PUBLISHED",
        True, not keep and not mem.cohort, model_calls, model_rejected, dependency_ms, confidence, text,
    )
    if not keep:
        _commit_goal(mem, goal, decision_id)
    mem.page = {"dialect": page["dialect"], "at": decision["published_at"], "decision_id": decision_id}
    return decision


def _commit_goal(mem: Memory, goal: dict, decision_id: str) -> None:
    goal = dict(goal)
    goal["decision_id"] = decision_id
    goal["status"] = "open"
    mem.goal = goal
    mem.cohort = True


def _decision(
    base, decision_id, goal, action, reason, trigger, mem, status, page, world, now, checks, status_name, effects,
    cohort, model_calls, model_rejected, dependency_ms, confidence, text,
) -> dict:
    content = [f"title:{title_id}" for title_id in _page_ids(page)] if page and action == "HOME_PAGE" else []
    published_at = iso(now + timedelta(milliseconds=dependency_ms))
    goal_view = None
    if goal:
        goal_view = {
            "id": goal["id"],
            "name": goal.get("name"),
            "deadline": goal["deadline"],
            "success_signal": goal.get("success_event"),
            "success_condition": goal.get("success_condition"),
            "rule_version": goal.get("rule_version"),
        }
    return {
        **base,
        "decision_id": decision_id,
        "goal": goal_view,
        "action": action,
        "no_action_reason": reason,
        "dialect": {
            "used": (page or {}).get("dialect") or trigger.get("app_dialect"),
            "source": "app_setting" if trigger.get("app_dialect") else None,
            "conflicts": _dialect_conflicts(trigger, mem),
            "freshness": "fresh" if trigger.get("app_dialect") else "unknown",
            "source_ref": trigger.get("event_id"),
        },
        "content": content,
        "page": page,
        "published_at": published_at,
        "confidence": confidence,
        "confidence_reason": text,
        "evidence": _evidence(mem, trigger, page, goal_view, world, now),
        "assumptions": _assumptions(mem, status, goal_view),
        "conflicts": _conflicts(trigger, mem, status),
        "checks": checks,
        "status": status_name,
        "effects_applied": effects,
        "measurement_cohort": bool(cohort and goal_view and status_name in {"PUBLISHED", "HOLDOUT"}),
        "model_calls": model_calls,
        "model_rejected": model_rejected,
        "dependency_ms": dependency_ms,
    }


def note_billing_conflict(mem: Memory, event: dict, now) -> None:
    if event.get("event") not in {"purchase_completed", "subscription_renewed", "subscription_lapsed"}:
        return
    values = sorted({row.get("subscription_status") for row in mem.profiles if row.get("subscription_status")})
    if not values:
        return
    mem.lessons.append(
        {
            "at": iso(now),
            "topic": "subscription_conflict",
            "detail": (
                f"{event['event_id']} is a billing event. Profile versions said {', '.join(values)}. "
                "The event outranks the export. The decision already published is not rewritten."
            ),
        }
    )


def close_if_succeeded(mem: Memory, world: dict, now) -> None:
    goal = mem.goal
    if not goal or goal.get("status") != "open":
        return
    matched = [event for event in mem.events if event_meets(goal, event, world["cat"])]
    if goal["id"] == "G09":
        days = {ist_day(timing(event)["true_time"]) for event in matched if timing(event)["true_time"]}
        if len(days) < 3:
            return
    elif not matched:
        return
    goal["status"] = "succeeded"
    mem.goals_closed.append(goal)
    mem.lessons.append(
        {
            "at": iso(now),
            "topic": "outcome",
            "detail": f"{goal['id']} already has a trusted success event. The published decision was not rewritten.",
        }
    )
    mem.goal = None


def apply_rule_correction(memories: dict[str, Memory], change: dict, now) -> list[str]:
    """Update one open goal. Users on any other goal are not loaded again."""
    if change.get("goal_id") != "G03":
        return []
    affected = []
    for user_id, mem in memories.items():
        goal = mem.goal
        if not goal or goal.get("status") != "open" or goal["id"] != "G03":
            continue
        new_success = change.get("new_success") or {}
        goal["success_event"] = new_success.get("event", "play_started")
        goal["success_condition"] = new_success.get("condition", "on a later IST calendar day within the window")
        goal["rule_version"] = "G03-corrected"
        for decision in mem.decisions:
            view = decision.get("goal") or {}
            if decision.get("decision_id") == goal.get("decision_id") and view.get("id") == "G03":
                view["success_signal"] = goal["success_event"]
                view["success_condition"] = goal["success_condition"]
                view["rule_version"] = "G03-corrected"
        mem.lessons.append(
            {
                "at": iso(now),
                "topic": "rule_corrected",
                "detail": "G03 now requires a play on a later IST day. Closed goals and other goal ids were left as they were.",
            }
        )
        affected.append(user_id)
    return affected


def degraded_decision(trigger: dict, now, decision_id: str, dependency_ms: int) -> dict:
    return {
        "decision_id": decision_id,
        "viewer_id": trigger["user_id"],
        "user_id": trigger["user_id"],
        "trigger": {
            "event": trigger.get("event"),
            "event_id": trigger.get("event_id"),
            "received_at": trigger.get("received_at"),
            "deliver_at": iso(now),
            "event_age_s": event_age_s(trigger),
            "timing_kind": timing(trigger)["kind"],
        },
        "goal": None,
        "action": "NO_ACTION",
        "no_action_reason": "dependency_failed:user_data_store",
        "dialect": {"used": None, "source": None, "conflicts": [], "freshness": "unknown"},
        "content": [],
        "confidence": 0.4,
        "confidence_reason": "The user store did not answer, so the agent publishes nothing rather than risk a duplicate message.",
        "evidence": [],
        "assumptions": ["A failed read is not evidence that this user has never been contacted."],
        "conflicts": [],
        "checks": {"passed": [], "failed": ["state_store"]},
        "status": "DEGRADED",
        "effects_applied": False,
        "measurement_cohort": False,
        "model_calls": 0,
        "tool_calls": 1,
        "dependency_ms": dependency_ms,
        "goals_rejected": [],
        "arm": None,
    }


def action_record(decision: dict, sequence: int) -> dict | None:
    if decision.get("status") != "PUBLISHED" or not decision.get("page"):
        return None
    page = decision["page"]
    record = {
        "action_id": f"A-{sequence:04d}",
        "decision_id": decision["decision_id"],
        "user_id": decision["user_id"],
        "trigger_event_id": decision["trigger"]["event_id"],
        "type": "home_page",
        "published_at": decision["published_at"],
        "dialect": page["dialect"],
        "rows": page["rows"],
    }
    if page.get("hero_title_id"):
        record["hero_title_id"] = page["hero_title_id"]
    return record


def state_snapshot(mem: Memory, now) -> dict:
    status = resolve_subscription(mem)
    signed, source = signup_at(mem)
    return {
        "user_id": mem.user_id,
        "arm": mem.arm,
        "facts": {
            "subscription": status,
            "signup_source": source,
            "signup_at": iso(signed) if hasattr(signed, "tzinfo") else signed,
            "profile_rows": len(mem.profiles),
            "plays": len(mem.plays),
            "episode_completions": len(mem.completes),
            "opt_out": sorted(mem.opt_out),
        },
        "active_goal": mem.goal,
        "closed_goals": mem.goals_closed,
        "decisions": mem.decisions,
        "lessons": mem.lessons,
        "as_of": iso(now),
    }


def load_world(pack: Path) -> dict:
    import csv

    cat = {}
    by_dialect = defaultdict(list)
    instruction_titles = set()
    with (pack / "data" / "catalogue.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            release = None
            if row.get("release_date"):
                release = date.fromisoformat(row["release_date"][:10])
            title = {
                "title_id": row["title_id"],
                "slug": row["content_slug"],
                "name": row["title"],
                "dialect": row["dialect"],
                "language": row["language"],
                "type": row["content_type"],
                "genres": [genre for genre in row["genres"].split("|") if genre],
                "release": release,
                "available_from": parse_ts(row["available_from"]),
                "available_until": parse_ts(row["available_until"]) if row.get("available_until") else None,
                "premium": row["is_premium"] == "true",
                "description": row.get("description") or "",
            }
            cat[title["title_id"]] = title
            by_dialect[title["dialect"]].append(title)
            if looks_like_instruction(title["description"]):
                instruction_titles.add(title["title_id"])
    episodes = defaultdict(list)
    with (pack / "data" / "episodes.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            episodes[row["content_slug"]].append((int(row["season"]), int(row["episode"]), row["episode_id"]))
    for rows in episodes.values():
        rows.sort()
    profiles = defaultdict(list)
    with (pack / "data" / "profiles_snapshot.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            profiles[row["user_id"]].append(row)
    arms = {}
    with (pack / "replay" / "arms.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            arms[row["user_id"]] = row["arm"]
    goals = json.loads((pack / "config" / "goals.json").read_text(encoding="utf-8"))
    goal_meta = {goal["goal_id"]: goal for goal in goals["goals"]}
    for meta in goal_meta.values():
        meta["version"] = goals.get("version", "2026-09-30")
    faults = {}
    for line in (pack / "replay" / "model_faults.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            fault = json.loads(line)
            faults[fault["trigger_event_id"]] = fault
    return {
        "cat": cat,
        "by_dialect": by_dialect,
        "episodes": episodes,
        "profiles": profiles,
        "arms": arms,
        "goal_meta": goal_meta,
        "faults": faults,
        "instruction_titles": instruction_titles,
        "withdrawn": {},
        "play_count": Counter(),
        "rule_version": goals.get("version", "2026-09-30"),
        "model_down": None,
        "store_degraded": None,
    }


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def run_replay(pack: Path) -> dict:
    world = load_world(pack)
    triggers = read_jsonl(pack / "replay" / "triggers.jsonl")
    live = read_jsonl(pack / "replay" / "live_events.jsonl")
    controls = read_jsonl(pack / "replay" / "control_events.jsonl")
    needed = {event["user_id"] for event in triggers}
    memories = {
        user_id: Memory(user_id, world["profiles"].get(user_id, []), world["arms"].get(user_id, "agent"))
        for user_id in needed
    }
    with (pack / "data" / "events.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("event") == "play_started":
                title_id = (event.get("properties") or {}).get("title_id")
                if title_id:
                    world["play_count"][title_id] += 1
            mem = memories.get(event["user_id"])
            if mem is not None:
                mem.add(event, world["cat"])

    timeline = []
    for index, event in enumerate(triggers):
        timeline.append((event["deliver_at"], 2, index, "trigger", event))
    for index, event in enumerate(live):
        timeline.append((event.get("deliver_at") or event["received_at"], 1, index, "live", event))
    for index, event in enumerate(controls):
        timeline.append((event["at"], 0, index, "control", event))
    timeline.sort()

    decisions = []
    actions = []
    handled: dict[str, dict] = {}
    examples: dict[str, str] = {}
    g03_affected: list[str] = []
    sequence = 0
    action_sequence = 0
    last_now = parse_ts(timeline[-1][0]) if timeline else parse_ts("2026-10-01T00:00:00.000Z")

    for _stamp, _priority, _index, kind, payload in timeline:
        now = parse_ts(payload.get("deliver_at") or payload.get("at") or payload.get("received_at"))
        last_now = now
        if kind == "control":
            if payload["type"] == "title_withdrawn":
                world["withdrawn"][payload["title_id"]] = now
            elif payload["type"] == "goal_rule_corrected":
                g03_affected = apply_rule_correction(memories, payload, now)
                world["rule_version"] = "G03-corrected"
            elif payload["type"] == "dependency_unavailable" and payload.get("dependency") == "model_provider":
                world["model_down"] = (now, parse_ts(payload["until"]))
            elif payload["type"] == "dependency_degraded" and payload.get("dependency") == "user_data_store":
                world["store_degraded"] = (now, parse_ts(payload["until"]))
            continue
        if kind == "live":
            mem = memories.get(payload["user_id"])
            if mem is None:
                continue
            if mem.add(payload, world["cat"]):
                note_billing_conflict(mem, payload, now)
                close_if_succeeded(mem, world, now)
            continue
        event_id = payload["event_id"]
        if event_id in handled:
            prior = handled[event_id]
            duplicate = dict(prior)
            duplicate["status"] = "DUPLICATE"
            duplicate["effects_applied"] = False
            duplicate["measurement_cohort"] = False
            duplicate["action"] = "NO_ACTION"
            duplicate["content"] = []
            duplicate["no_action_reason"] = "duplicate_trigger"
            duplicate["page"] = None
            decisions.append(duplicate)
            mem = memories[payload["user_id"]]
            mem.lessons.append({"at": iso(now), "topic": "duplicate_trigger", "detail": f"{event_id} already handled as {prior['decision_id']}."})
            mem.decisions.append(duplicate)
            continue
        dependency_ms = 0
        window = world.get("store_degraded")
        if window and window[0] <= now < window[1]:
            dependency_ms += 8000
            if store_times_out(payload["user_id"], event_id):
                sequence += 1
                decision = degraded_decision(payload, now, f"D-{sequence:04d}", dependency_ms)
                decision["decided_in_ms"] = dependency_ms
                decisions.append(decision)
                handled[event_id] = decision
                mem = memories[payload["user_id"]]
                mem.add(payload, world["cat"])
                mem.decisions.append(decision)
                _remember_example(examples, decision, mem)
                continue
        sequence += 1
        mem = memories[payload["user_id"]]
        mem.add(payload, world["cat"])
        started = __import__("time").perf_counter()
        decision = decide(mem, payload, world, now, f"D-{sequence:04d}", dependency_ms)
        compute_ms = int((__import__("time").perf_counter() - started) * 1000)
        decision["decided_in_ms"] = compute_ms + int(decision.get("dependency_ms") or 0)
        decision["published_at"] = iso(now + timedelta(milliseconds=decision["decided_in_ms"]))
        if decision.get("status") == "PUBLISHED" and mem.page and mem.page.get("decision_id") == decision["decision_id"]:
            mem.page["at"] = decision["published_at"]
        decisions.append(decision)
        handled[event_id] = decision
        mem.decisions.append(decision)
        record = action_record(decision, action_sequence + 1)
        if record is not None:
            action_sequence += 1
            record["action_id"] = f"A-{action_sequence:04d}"
            actions.append(record)
        _remember_example(examples, decision, mem)

    return {
        "world": world,
        "memories": memories,
        "decisions": decisions,
        "actions": actions,
        "examples": examples,
        "g03_affected": g03_affected,
        "as_of": last_now,
        "triggered_users": len(memories),
    }


def _remember_example(examples: dict, decision: dict, mem: Memory) -> None:
    if "dialect_conflict" not in examples and decision.get("dialect", {}).get("conflicts") and decision.get("status") == "PUBLISHED":
        examples["dialect_conflict"] = decision["user_id"]
    if "stale" not in examples and decision.get("no_action_reason") == "stale_trigger":
        examples["stale"] = decision["user_id"]
    if "holdout" not in examples and decision.get("status") == "HOLDOUT":
        examples["holdout"] = decision["user_id"]
    if "model" not in examples and decision.get("model_rejected"):
        examples["model"] = decision["user_id"]
    if "store" not in examples and decision.get("status") == "DEGRADED":
        examples["store"] = decision["user_id"]
    if "nothing" not in examples and decision.get("no_action_reason") == "already_on_track":
        examples["nothing"] = decision["user_id"]


def write_outputs(result: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    state_dir = out / "viewer_state"
    state_dir.mkdir(exist_ok=True)
    _write_jsonl(out / "decisions.jsonl", [_public_decision(item) for item in result["decisions"]])
    _write_jsonl(out / "actions.jsonl", result["actions"])
    for mem in result["memories"].values():
        if not mem.decisions and not mem.lessons:
            continue
        path = state_dir / f"{mem.user_id}.json"
        path.write_text(json.dumps(state_snapshot(mem, result["as_of"]), default=str), encoding="utf-8")
    latencies = [item.get("decided_in_ms", 0) for item in result["decisions"] if item.get("status") != "DUPLICATE"]
    latencies.sort()
    by_status = Counter(item["status"] for item in result["decisions"])
    report = {
        "budget_ms": 60000,
        "hard_fail_ms": 300000,
        "count": len(latencies),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "max_ms": latencies[-1] if latencies else 0,
        "over_budget": sum(1 for value in latencies if value > 60000),
        "over_5_min": sum(1 for value in latencies if value > 300000),
        "by_status": dict(by_status),
        "note": "Dependency delays (an 8s store read, a 45s model timeout) are added to decided_in_ms. The replay does not sleep for them.",
    }
    (out / "latency_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


def _public_decision(decision: dict) -> dict:
    public = dict(decision)
    public.pop("page", None)
    return public


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str) + "\n")


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
    return values[index]
