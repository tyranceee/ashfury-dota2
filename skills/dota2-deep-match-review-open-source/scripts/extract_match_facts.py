#!/usr/bin/env python3
"""Extract a deterministic fact ledger from an OpenDota match JSON.

This script does not grade players. It gathers evidence and reports source-data
gaps so the reviewing agent can finish the mandatory analysis templates before
drafting the final review.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


MINUTES = (0, 1, 2, 3, 4, 5, 6, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60)
STRICT_FIELDS = (
    "players",
    "version",
    "teamfights",
    "objectives",
    "radiant_gold_adv",
    "radiant_xp_adv",
)
PLACEHOLDER_VALUES = {
    "",
    "PENDING",
    "NOT_COMPLETE",
    "NOT_ALLOWED",
    "TODO",
    "TBD",
}
EVIDENCE_MARKERS = (
    "数据明确显示",
    "高概率推断",
    "无法确认",
    "模型判断",
    "玩家复述",
    "经验估计",
    "data fact",
    "inference",
    "unknown",
)

# Requirements are maintained here, never supplied by the completed ledger.
SCHEMA_VERSION = 2
RESOURCE_CATEGORIES = ("damage_targets", "buildings", "roshan", "buybacks")
SECTION_TITLES = (
    "全局博弈", "比赛结论与数据边界", "阵容与三路对线", "节奏与关键团战",
    "装备与技能决策", "十人职责与用户死亡", "本人对局改进意见",
)
LEGACY_SECTION_TITLES = (
    "比赛结论与用户概览", "数据完整度与限制", "阵容胜利条件", "全局博弈思路",
    "三路对线", "中期节奏与经济/经验拐点", "关键装备、黑皇杖与强势窗口", "关键技能矩阵",
    "决定性团战", "六核独立审计与同位置比较", "四辅助职责", "用户每次死亡与个人改进",
    "责任排序或贡献排序", "最终结论", "完整性验收摘要",
)
GAMEPLAN_FIELDS = (
    "core_question", "resource", "plan_a", "plan_b", "observable_signals",
    "actual_choices", "result", "adjustment", "counterevidence", "decision_quality",
)
PRE_LANE_FIELDS = (
    "assumptions", "verdict", "mechanisms", "phase_windows", "radiant_plan", "dire_plan",
)
RECORD_FIELDS = {
    "global_gameplans": GAMEPLAN_FIELDS,
    "decisive_fights": GAMEPLAN_FIELDS + (
        "pre_fight", "engagement", "first_phase", "second_phase", "engine_enabler",
        "resolution", "map_conversion", "contribution_and_error",
    ),
    "lanes": ("matchup", "minute_5_10", "support_damage_0_6", "early_events",
              "rotation_boundary", "minute_10_15", "first_tower", "conclusion", "expectation_vs_actual"),
    "support_lane_pressure": ("baseline_0", "cumulative_6", "net_damage", "lane_conversion"),
    "cores": ("role_basis", "primary_secondary_roles", "enable_and_limit", "economy_curve",
              "lane_and_recovery", "item_windows", "participation_and_targets", "key_skills",
              "team_enabling", "deaths_buybacks", "map_conversion", "conclusion", "comparison",
              "bkb_necessity", "bkb_tradeoff"),
    "supports": ("role_basis", "lane_conversion", "vision", "control_and_saves",
                 "key_deaths", "equipment_fit", "conclusion"),
    "key_skills": ("role", "match_vs_window_uses",
                   "synergy", "output_window", "map_conversion"),
    "user_deaths": ("time", "task_and_state", "recorded_combatants", "cause", "visible_signals",
                    "choice", "team_outcome", "role_completion", "classification", "alternative"),
    "resource_categories": ("finding", "consequence"),
    "training": ("signal", "action", "tradeoff"),
}


def source_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def analysis_template() -> dict[str, Any]:
    return {**{key: [] for key in RECORD_FIELDS}, "fight_selection": {
        "reason": "PENDING", "timeline_crosscheck": "PENDING", "evidence": {},
    }}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def unwrap_match(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict) and isinstance(raw.get("players"), list):
        return raw
    if isinstance(raw, dict):
        for key in ("match", "data", "result"):
            value = raw.get(key)
            if isinstance(value, dict) and isinstance(value.get("players"), list):
                return value
    raise ValueError("JSON does not contain an OpenDota match object with players")


def recent_matches(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in ("matches", "data", "results", "items"):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def value_at(series: Any, minute: int, times: Any) -> Any:
    """Select an exact game-time sample; never infer minutes from array indices."""
    if (not isinstance(series, list) or not isinstance(times, list)
            or len(series) != len(times) or not times
            or not all(finite_number(t) for t in times)
            or any(a >= b for a, b in zip(times, times[1:]))):
        return None
    target = minute * 60
    if target in times:
        value = series[times.index(target)]
        return value if finite_number(value) else None
    return None


def nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, dict, str)):
        return len(value) > 0
    return True


def substantive(value: Any) -> bool:
    """Return whether an analysis field contains more than a placeholder."""
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip()
        caveat = re.match(r"^(无法确认|不适用|未知|unknown|not_applicable)\s*[:：]\s*(.*)$", text, re.I | re.S)
        if caveat:
            return substantive(caveat.group(2))
        return text.upper() not in PLACEHOLDER_VALUES and text not in {
            "无法确认", "不适用", "未知", "已完成", "待补充", "待确认",
        } and text.lower() not in {"unknown", "not_applicable"}
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def evidence_labeled(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    lowered = value.lower()
    return any(marker in lowered for marker in EVIDENCE_MARKERS)


def aggregate_damage_targets(raw: Any) -> list[dict[str, Any]]:
    totals: defaultdict[str, float] = defaultdict(float)
    if isinstance(raw, dict):
        for targets in raw.values():
            if not isinstance(targets, dict):
                continue
            for target, amount in targets.items():
                if isinstance(amount, (int, float)):
                    totals[str(target)] += amount
    return [
        {"target": target, "damage": amount}
        for target, amount in sorted(totals.items(), key=lambda item: item[1], reverse=True)
    ]


def purchase_timeline(player: dict[str, Any]) -> list[dict[str, Any]]:
    log = player.get("purchase_log")
    if isinstance(log, list):
        cleaned = [item for item in log if isinstance(item, dict)
                   and finite_number(item.get("time")) and isinstance(item.get("key"), str)]
        return sorted(cleaned, key=lambda item: item["time"])
    # OpenDota purchase_time sums purchase timestamps; it is not an event log.
    return []


def minute_points(player: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for minute in MINUTES:
        result[str(minute)] = {
            "gold": value_at(player.get("gold_t"), minute, player.get("times")),
            "xp": value_at(player.get("xp_t"), minute, player.get("times")),
            "last_hits": value_at(player.get("lh_t"), minute, player.get("times")),
            "denies": value_at(player.get("dn_t"), minute, player.get("times")),
            "hero_damage": value_at(player.get("hero_damage_t"), minute, player.get("times")),
        }
    return result


def lane_damage(player: dict[str, Any]) -> dict[str, Any]:
    series = player.get("hero_damage_t")
    cumulative = {str(minute): value_at(series, minute, player.get("times")) for minute in range(7)}
    baseline = cumulative["0"]
    at_six = cumulative["6"]
    net = at_six - baseline if isinstance(baseline, (int, float)) and isinstance(at_six, (int, float)) else None
    return {"cumulative_0_to_6": cumulative, "baseline_0": baseline, "minute_6": at_six, "net_after_horn": net}


def player_fact(player: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "index": index,
        "player_slot": player.get("player_slot"),
        "radiant": player.get("isRadiant", player.get("player_slot", 128) < 128 if isinstance(player.get("player_slot"), int) else None),
        "account_id": player.get("account_id"),
        "hero_id": player.get("hero_id"),
        "name": player.get("personaname") or player.get("name"),
        "lane": player.get("lane"),
        "lane_role": player.get("lane_role"),
        "lane_efficiency_pct": player.get("lane_efficiency_pct"),
        "scoreboard": {
            "kills": player.get("kills"),
            "deaths": player.get("deaths"),
            "assists": player.get("assists"),
            "last_hits": player.get("last_hits"),
            "denies": player.get("denies"),
            "gpm": player.get("gold_per_min"),
            "xpm": player.get("xp_per_min"),
            "level": player.get("level"),
            "net_worth": player.get("net_worth"),
            "hero_damage": player.get("hero_damage"),
            "tower_damage": player.get("tower_damage"),
            "hero_healing": player.get("hero_healing"),
            "stuns": player.get("stuns"),
            "teamfight_participation": player.get("teamfight_participation"),
        },
        "minute_curve": minute_points(player),
        "curve_time_basis": "Exact times entries in game seconds; null means no valid aligned sample, not zero.",
        "field_status": {key: "missing" if key not in player else "null" if player[key] is None
                         else "empty" if player[key] in ([], {}) else "present"
                         for key in ("times", "gold_t", "xp_t", "lh_t", "dn_t", "hero_damage_t",
                                     "purchase_log", "item_uses", "ability_uses", "ability_targets",
                                     "damage_targets", "kills_log", "deaths_log", "buyback_log")},
        "lane_damage_0_to_6": lane_damage(player),
        "final_items": [player.get(f"item_{slot}") for slot in range(6)],
        "purchases": purchase_timeline(player),
        "purchase_summary": {key: player.get(key) for key in
                             ("purchase", "purchase_time", "first_purchase_time")},
        "purchase_time_basis": "purchases contains valid purchase_log events only; summary timestamps are not a complete timeline.",
        "item_uses": player.get("item_uses") or {},
        "ability_uses": player.get("ability_uses") or {},
        "ability_targets": player.get("ability_targets") or {},
        "damage_by_target": aggregate_damage_targets(player.get("damage_targets")),
        "killed_by": player.get("killed_by") or {},
        "kills_log": player.get("kills_log") or [],
        "deaths_log": player.get("deaths_log"),
        "buyback_log": player.get("buyback_log") or [],
        "permanent_buffs": player.get("permanent_buffs") or [],
        "vision": {
            "obs_placed": player.get("obs_placed"),
            "sen_placed": player.get("sen_placed"),
            "observer_kills": player.get("observer_kills"),
            "sentry_kills": player.get("sentry_kills"),
        },
    }


def death_timeline(players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deaths: list[dict[str, Any]] = []
    for killer_index, player in enumerate(players):
        for event in player.get("kills_log") or []:
            if isinstance(event, dict):
                deaths.append(
                    {
                        "time": event.get("time"),
                        "victim": event.get("key"),
                        "killer_index": killer_index,
                        "killer_hero_id": player.get("hero_id"),
                    }
                )
    return sorted(deaths, key=lambda event: (event.get("time") is None, event.get("time") or 0))


def advantage_swings(match: dict[str, Any]) -> list[dict[str, Any]]:
    gold = match.get("radiant_gold_adv") or []
    xp = match.get("radiant_xp_adv") or []
    count = max(len(gold), len(xp))
    swings = []
    for minute in range(1, count):
        gold_delta = gold[minute] - gold[minute - 1] if minute < len(gold) else None
        xp_delta = xp[minute] - xp[minute - 1] if minute < len(xp) else None
        magnitude = abs(gold_delta or 0) + abs(xp_delta or 0)
        swings.append({"minute": minute, "gold_delta": gold_delta, "xp_delta": xp_delta, "magnitude": magnitude})
    return sorted(swings, key=lambda row: row["magnitude"], reverse=True)[:12]


def teamfight_facts(match: dict[str, Any], players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for fight_index, fight in enumerate(match.get("teamfights") or [], start=1):
        if not isinstance(fight, dict):
            continue
        fight_players = []
        for index, data in enumerate(fight.get("players") or []):
            if not isinstance(data, dict):
                continue
            fight_players.append(
                {
                    "index": index,
                    "hero_id": players[index].get("hero_id") if index < len(players) else None,
                    "damage": data.get("damage"),
                    "deaths": data.get("deaths"),
                    "killed": data.get("killed") or {},
                    "gold_delta": data.get("gold_delta"),
                    "xp_delta": data.get("xp_delta"),
                    "ability_uses": data.get("ability_uses") or {},
                    "item_uses": data.get("item_uses") or {},
                }
            )
        result.append({"index": fight_index, "start": fight.get("start"), "end": fight.get("end"), "players": fight_players})
    return result


def side_from_team_code(team: Any) -> str | None:
    if team == 2:
        return "radiant"
    if team == 3:
        return "dire"
    return None


def player_index_from_objective(event: dict[str, Any], players: list[dict[str, Any]]) -> int | None:
    slot = event.get("slot")
    if isinstance(slot, int) and 0 <= slot < len(players):
        return slot
    player_slot = event.get("player_slot")
    for index, player in enumerate(players):
        if player.get("player_slot") == player_slot:
            return index
    return None


def roshan_aegis_lifecycles(match: dict[str, Any], players: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Separate Roshan kill ownership from Aegis pickup/steal ownership."""
    objectives = [event for event in match.get("objectives") or [] if isinstance(event, dict)]
    roshan_events = [event for event in objectives if event.get("type") == "CHAT_MESSAGE_ROSHAN_KILL"]
    aegis_types = {"CHAT_MESSAGE_AEGIS", "CHAT_MESSAGE_AEGIS_STOLEN"}
    result = []
    for index, roshan in enumerate(roshan_events):
        start = roshan.get("time")
        next_start = roshan_events[index + 1].get("time") if index + 1 < len(roshan_events) else None
        candidates = []
        for event in objectives:
            event_time = event.get("time")
            if event.get("type") not in aegis_types or not isinstance(event_time, (int, float)):
                continue
            if not isinstance(start, (int, float)) or event_time < start:
                continue
            if isinstance(next_start, (int, float)) and event_time >= next_start:
                continue
            candidates.append(event)
        aegis = min(candidates, key=lambda event: event.get("time", 10**12)) if candidates else None
        holder_index = player_index_from_objective(aegis, players) if aegis else None
        holder_side = None
        holder_hero_id = None
        holder_player_slot = None
        if holder_index is not None:
            holder = players[holder_index]
            player_slot = holder.get("player_slot")
            if isinstance(player_slot, int):
                holder_side = "radiant" if player_slot < 128 else "dire"
            holder_hero_id = holder.get("hero_id")
            holder_player_slot = holder.get("player_slot")
        kill_side = side_from_team_code(roshan.get("team"))
        if not aegis:
            relation = "unresolved"
        elif aegis.get("type") == "CHAT_MESSAGE_AEGIS_STOLEN":
            relation = "stolen"
        elif kill_side and holder_side:
            relation = "same_side" if kill_side == holder_side else "opposing_side"
        else:
            relation = "unresolved"
        result.append(
            {
                "sequence": index + 1,
                "roshan_kill_time": start,
                "roshan_kill_team_code": roshan.get("team"),
                "roshan_kill_side": kill_side,
                "aegis_event_time": aegis.get("time") if aegis else None,
                "aegis_event_type": aegis.get("type") if aegis else None,
                "aegis_holder_index": holder_index,
                "aegis_holder_player_slot": holder_player_slot,
                "aegis_holder_hero_id": holder_hero_id,
                "aegis_holder_side": holder_side,
                "ownership_relation": relation,
            }
        )
    return result


def purchased_item(player: dict[str, Any], item_key: str) -> bool:
    if any(event.get("key") == item_key for event in purchase_timeline(player)):
        return True
    # Summary keys can establish a purchase, but cannot reconstruct its time.
    return any(isinstance(player.get(key), dict) and item_key in player[key]
               for key in ("purchase_time", "first_purchase_time")) or (
        isinstance(player.get("purchase"), dict)
        and finite_number(player["purchase"].get(item_key)) and player["purchase"][item_key] > 0)


def equipment_analysis_template(
    match: dict[str, Any], user_account_id: Any, aegis_facts: list[dict[str, Any]]
) -> dict[str, Any]:
    players = match.get("players") or []
    user_index = next(
        (index for index, player in enumerate(players)
         if user_account_id is not None and player.get("account_id") is not None
         and str(player.get("account_id")) == str(user_account_id)),
        None,
    )
    bkb_indices = []
    for index, player in enumerate(players):
        uses = (player.get("item_uses") or {}).get("black_king_bar", 0)
        window_use = any(
            isinstance(fight, dict) and isinstance(fight.get("players"), list)
            and index < len(fight["players"]) and isinstance(fight["players"][index], dict)
            and bool((fight["players"][index].get("item_uses") or {}).get("black_king_bar"))
            for fight in match.get("teamfights") or []
        )
        if purchased_item(player, "black_king_bar") or uses or window_use:
            bkb_indices.append(index)
    player_audits = []
    for index, player in enumerate(players):
        player_audits.append(
            {
                "player_index": index,
                "hero_id": player.get("hero_id"),
                "role": "PENDING",
                "key_items": [],
                "build_conclusion": "PENDING",
                "resource_fit": "PENDING",
                "evidence_level": "PENDING",
                "evidence": {},
            }
        )
    bkb_audits = [
        {
            "player_index": index,
            "purchase_time": "PENDING",
            "window_analysis": "PENDING",
            "conclusion": "PENDING",
            "evidence_level": "PENDING",
            "evidence": {},
        }
        for index in bkb_indices
    ]
    aegis_audits = []
    for fact in aegis_facts:
        aegis_audits.append(
            {
                **fact,
                "first_relevant_fight": "PENDING",
                "first_life_result": "PENDING",
                "death_type_boundary": "PENDING",
                "second_life_result": "PENDING",
                "map_conversion": "PENDING",
                "evidence_level": "PENDING",
                "evidence": {},
            }
        )
    return {
        "required_player_indices": list(range(len(players))),
        "required_bkb_player_indices": bkb_indices,
        "user_index": user_index,
        "player_item_audits": player_audits,
        "bkb_player_audits": bkb_audits,
        "aegis_lifecycle_audits": aegis_audits,
        "user_build_path": {
            "first_fight_ready_item": "PENDING",
            "enemy_problem_answered": "PENDING",
            "highest_risk_item": "PENDING",
            "item_gap_consequence": "PENDING",
            "alternative_order": "PENDING",
            "next_game_rule": "PENDING",
            "evidence_level": "PENDING",
            "evidence": {},
        },
        "high_risk_items_buyback_conflict": "PENDING",
        "high_risk_items_buyback_evidence_level": "PENDING",
        "high_risk_items_buyback_evidence": {},
        "bkb_team_plan": {
            side: {"allocation": "PENDING", "tradeoff": "PENDING", "evidence": {}}
            for side in ("radiant", "dire")
        },
        "rule": "Timestamps alone do not complete an item audit; every key item needs capability, fight, use, outcome, conversion, and evidence fields.",
    }


def find_recent_status(recent_raw: Any, match_id: Any) -> dict[str, Any]:
    for item in recent_matches(recent_raw):
        if str(item.get("match_id")) == str(match_id):
            return {key: item.get(key) for key in ("parsed", "has_parsed", "parse_status") if key in item}
    return {}


def parse_audit(match: dict[str, Any], recent_status: dict[str, Any]) -> dict[str, Any]:
    presence = {field: nonempty(match.get(field)) for field in STRICT_FIELDS}
    players = match.get("players")
    presence["players"] = (
        isinstance(players, list) and len(players) == 10
        and all(isinstance(p, dict) and type(p.get("player_slot")) is int
                and type(p.get("hero_id")) is int and p["hero_id"] > 0 for p in players)
        and {p["player_slot"] for p in players} == {0, 1, 2, 3, 4, 128, 129, 130, 131, 132}
    )
    for key in ("teamfights", "objectives"):
        records = match.get(key)
        presence[key] = isinstance(records, list) and all(isinstance(row, dict) for row in records)
    if presence["teamfights"]:
        presence["teamfights"] = all(
            type(row.get("start")) in (int, float) and type(row.get("end")) in (int, float)
            and row["start"] < row["end"]
            and isinstance(row.get("players"), list) and len(row["players"]) == 10
            and all(isinstance(player, dict) for player in row["players"])
            for row in match["teamfights"]
        )
    if presence["objectives"]:
        presence["objectives"] = all(type(row.get("time")) in (int, float)
                                     and isinstance(row.get("type"), str) and bool(row["type"])
                                     for row in match["objectives"])
    for key in ("radiant_gold_adv", "radiant_xp_adv"):
        curve = match.get(key)
        presence[key] = isinstance(curve, list) and bool(curve) and all(type(v) in (int, float) for v in curve)
    presence["version"] = type(match.get("version")) is int and match["version"] > 0
    presence["match_id"] = type(match.get("match_id")) is int and match["match_id"] > 0
    presence["start_time"] = type(match.get("start_time")) is int and match["start_time"] > 0
    presence["duration"] = type(match.get("duration")) is int and match["duration"] > 0
    presence["radiant_win"] = type(match.get("radiant_win")) is bool
    presence["patch"] = nonempty(match.get("patch"))
    complete = all(presence.values())
    any_timeline = any(presence.get(field) for field in ("teamfights", "objectives", "radiant_gold_adv", "radiant_xp_adv"))
    tier = "complete" if complete else "partial" if presence["players"] and any_timeline else "summary"
    single_status = {key: match.get(key) for key in ("parsed", "has_parsed", "parse_status") if key in match}
    warnings = []
    statuses = {"single_match": single_status, "recent_list": recent_status}
    flat = {f"single.{key}": value for key, value in single_status.items()}
    flat.update({f"recent.{key}": value for key, value in recent_status.items()})
    claims_parsed = any(value is True or str(value).lower() in {"parsed", "complete", "completed"} for value in flat.values())
    claims_unparsed = any(value is False or str(value).lower() in {"waiting", "requested", "unavailable"} for value in flat.values())
    if claims_parsed and not complete:
        warnings.append("Status claims parsed, but one or more strict fields are missing or empty.")
    if claims_unparsed and complete:
        warnings.append("Status claims unparsed/incomplete, but all strict fields are present.")
    if recent_status and single_status and recent_status != single_status:
        warnings.append("Recent-list status and single-match cache status are inconsistent.")
    return {
        "actual_tier": tier,
        "strict_complete": complete,
        "field_presence": presence,
        "missing_for_full_review": [field for field, present in presence.items() if not present],
        "reported_status": statuses,
        "warnings": warnings,
        "empty_event_fields": [key for key in ("teamfights", "objectives") if match.get(key) == []],
        "note": "Valid empty event arrays are not missing data; confirm their coverage before drawing negative conclusions.",
    }


def coverage_template(match: dict[str, Any], user_account_id: Any) -> dict[str, Any]:
    user = next((p for p in match.get("players") or [] if user_account_id is not None
                 and p.get("account_id") is not None and str(p.get("account_id")) == str(user_account_id)), None)
    user_deaths = user.get("deaths") if isinstance(user, dict) else None
    return {
        "draft_status": "NOT_ALLOWED",
        "lanes": {"required": 3, "completed": 0},
        "support_lane_pressure": {"required": 4, "completed": 0},
        "cores": {"required": 6, "completed": 0},
        "supports": {"required": 4, "completed": 0},
        "global_gameplans": {"required_range": [2, 3], "selected": 0, "completed": 0},
        "key_skills": {
            "required_each_side": [2, 4],
            "radiant_selected": 0,
            "radiant_completed": 0,
            "dire_selected": 0,
            "dire_completed": 0,
        },
        "decisive_fights": {
            "required_range": [3, 5],
            "selected": 0,
            "completed": 0,
            "gameplans_completed": 0,
        },
        "user_deaths": {"required": user_deaths, "completed": 0},
        "resource_categories": {
            "required": ["damage_targets", "buildings", "roshan", "buybacks"],
            "completed": [],
        },
        "equipment_validation": "PENDING",
        "evidence_labels": "PENDING",
        "rule": "Legacy display counters only. Version 2 gates derive coverage from analysis records and source facts; editing these values cannot satisfy validation.",
    }


def require_fields(record: dict[str, Any], fields: tuple[str, ...], prefix: str, missing: list[str]) -> None:
    for field in fields:
        value = record.get(field)
        if field == "evidence_level":
            valid = evidence_labeled(value)
        elif isinstance(value, dict):
            valid = value.get("status") in {"unavailable", "not_applicable"} and substantive(value.get("reason"))
        else:
            valid = type(value) in (str, int, float) and substantive(value)
        if not valid:
            missing.append(f"{prefix}.{field} is incomplete")


def validate_windows(record: dict[str, Any], legacy_fields: tuple[str, str],
                     prefix: str, missing: list[str]) -> None:
    # New records describe actual relevant windows, without requiring opposite-valued examples.
    fields = ("window_analysis",) if "window_analysis" in record else legacy_fields
    require_fields(record, fields, prefix, missing)


def validate_training(rows: Any, ledger: dict[str, Any], prefix: str) -> list[str]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 3:
        return [f"{prefix}: requires 1-3 prioritized training items"]
    missing = []
    for i, row in enumerate(rows):
        label = f"{prefix}[{i}]"
        if not isinstance(row, dict):
            missing.append(f"{label}: training item must be an object")
            continue
        require_fields(row, ("signal", "action", "tradeoff"), label, missing)
        missing.extend(validate_evidence(row.get("evidence"), ledger, label))
    return missing


def validate_equipment_analysis(ledger: dict[str, Any]) -> list[str]:
    equipment = ledger.get("equipment_analysis_template")
    if not isinstance(equipment, dict):
        return ["equipment_analysis_template is missing"]
    missing: list[str] = []

    required_players = set(equipment.get("required_player_indices") or [])
    audits = equipment.get("player_item_audits") or []
    audit_map: dict[Any, dict[str, Any]] = {}
    for audit in audits:
        if not isinstance(audit, dict):
            missing.append("player_item_audits contains a non-object entry")
            continue
        index = audit.get("player_index")
        if type(index) is not int or index not in required_players:
            missing.append("player_item_audits contains an invalid player_index")
            continue
        if index in audit_map:
            missing.append(f"player_item_audits contains duplicate player_index={index}")
        audit_map[index] = audit
    for index in sorted(required_players):
        audit = audit_map.get(index)
        if not audit:
            missing.append(f"player_item_audits missing player_index={index}")
            continue
        detail = audit.get("detail", "detailed")
        if detail != "detailed":
            missing.append(f"player_item_audits[{index}]: full review requires detailed equipment analysis")
        fields = ("role", "build_conclusion", "resource_fit", "evidence_level")
        require_fields(
            audit,
            fields,
            f"player_item_audits[{index}]",
            missing,
        )
        if audit.get("role") not in {"core", "support", "核心", "辅助"}:
            missing.append(f"player_item_audits[{index}].role must identify core or support")
        if not evidence_labeled(audit.get("evidence_level")):
            missing.append(f"player_item_audits[{index}].evidence_level lacks a fact/inference/unknown label")
        key_items = audit.get("key_items")
        if not isinstance(key_items, list) or not key_items:
            missing.append(f"player_item_audits[{index}].key_items is empty")
            continue
        for item_index, item in enumerate(key_items):
            if not isinstance(item, dict):
                missing.append(f"player_item_audits[{index}].key_items[{item_index}] is not an object")
                continue
            require_fields(
                item,
                (
                    "item",
                    "purchase_time",
                    "capability_or_tradeoff",
                    "alternative_and_cost",
                    "targeting",
                    "first_relevant_fight",
                    "use_evidence",
                    "fight_outcome",
                    "map_conversion",
                    "window_judgment",
                    "evidence_level",
                ),
                f"player_item_audits[{index}].key_items[{item_index}]",
                missing,
            )
            if not evidence_labeled(item.get("evidence_level")):
                missing.append(
                    f"player_item_audits[{index}].key_items[{item_index}].evidence_level lacks a fact/inference/unknown label"
                )

    required_bkb = set(equipment.get("required_bkb_player_indices") or [])
    bkb_audits = equipment.get("bkb_player_audits") or []
    bkb_map = {audit.get("player_index"): audit for audit in bkb_audits if isinstance(audit, dict)}
    if (len(bkb_map) != len(bkb_audits) or not required_bkb <= set(bkb_map)
            or any(type(index) is not int or index not in required_players for index in bkb_map)):
        missing.append("bkb_player_audits must identify each required holder exactly once with valid player indices")
    required_bkb |= set(bkb_map)
    for index in sorted(required_bkb):
        audit = bkb_map.get(index)
        if not audit:
            missing.append(f"bkb_player_audits missing player_index={index}")
            continue
        require_fields(
            audit,
            ("purchase_time", "conclusion", "evidence_level"),
            f"bkb_player_audits[{index}]",
            missing,
        )
        validate_windows(audit, ("high_value_window", "low_value_or_uncovered_window"),
                         f"bkb_player_audits[{index}]", missing)
        if not evidence_labeled(audit.get("evidence_level")):
            missing.append(f"bkb_player_audits[{index}].evidence_level lacks a fact/inference/unknown label")

    expected_aegis = ledger.get("roshan_aegis_lifecycles") or []
    aegis_audits = equipment.get("aegis_lifecycle_audits") or []
    aegis_map = {
        audit.get("roshan_kill_time"): audit for audit in aegis_audits if isinstance(audit, dict)
    }
    if len(aegis_map) != len(aegis_audits) or set(aegis_map) != {fact.get("roshan_kill_time") for fact in expected_aegis}:
        missing.append("aegis_lifecycle_audits must identify each lifecycle exactly once")
    for fact in expected_aegis:
        time = fact.get("roshan_kill_time")
        audit = aegis_map.get(time)
        if not audit:
            missing.append(f"aegis_lifecycle_audits missing roshan_kill_time={time}")
            continue
        for factual_field in (
            "roshan_kill_side",
            "aegis_event_type",
            "aegis_holder_side",
            "ownership_relation",
            "aegis_holder_index",
            "aegis_holder_player_slot",
            "aegis_holder_hero_id",
        ):
            if audit.get(factual_field) != fact.get(factual_field):
                missing.append(
                    f"aegis_lifecycle_audits[{time}].{factual_field} does not match extracted facts"
                )
        require_fields(
            audit,
            (
                "first_relevant_fight",
                "first_life_result",
                "death_type_boundary",
                "second_life_result",
                "map_conversion",
                "evidence_level",
            ),
            f"aegis_lifecycle_audits[{time}]",
            missing,
        )
        if not evidence_labeled(audit.get("evidence_level")):
            missing.append(f"aegis_lifecycle_audits[{time}].evidence_level lacks a fact/inference/unknown label")

    if equipment.get("user_index") is not None:
        user_path = equipment.get("user_build_path") or {}
        require_fields(
            user_path,
            (
                "first_fight_ready_item",
                "enemy_problem_answered",
                "highest_risk_item",
                "item_gap_consequence",
                "alternative_order",
                "next_game_rule",
                "evidence_level",
            ),
            "user_build_path",
            missing,
        )
        if not evidence_labeled(user_path.get("evidence_level")):
            missing.append("user_build_path.evidence_level lacks a fact/inference/unknown label")
    require_fields(
        equipment,
        ("high_risk_items_buyback_conflict", "high_risk_items_buyback_evidence_level"),
        "equipment_analysis_template",
        missing,
    )
    if not evidence_labeled(equipment.get("high_risk_items_buyback_evidence_level")):
        missing.append("high_risk_items_buyback_evidence_level lacks a fact/inference/unknown label")
    return missing


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Resolve a JSON Pointer; references cannot point to the model's own analysis."""
    if not isinstance(pointer, str) or not pointer.startswith(("/source_match/", "/supplemental_sources/")):
        raise ValueError("reference must identify source data")
    value = document
    for part in pointer.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not key.isdecimal():
                raise ValueError("invalid list index")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError("reference traverses a scalar")
    return value


def validate_evidence(evidence: Any, ledger: dict[str, Any], prefix: str) -> list[str]:
    if not isinstance(evidence, dict):
        return [f"{prefix}: evidence object missing"]
    missing = []
    if evidence.get("source_type") not in {"parsed_data", "event_log", "replay", "player_recollection", "mixed"}:
        missing.append(f"{prefix}: invalid source_type")
    if evidence.get("judgment") not in {"数据明确显示", "模型判断", "高概率推断", "经验估计", "无法确认", "玩家复述"}:
        missing.append(f"{prefix}: invalid judgment")
    refs = evidence.get("refs")
    actual_sources = set()
    if not isinstance(refs, list) or not refs:
        missing.append(f"{prefix}: source refs required")
    else:
        for ref in refs:
            try:
                resolve_pointer(ledger, ref)
                if ref.startswith("/source_match/"):
                    actual_sources.add("parsed_data")
                else:
                    source_id = ref.split("/")[2].replace("~1", "/").replace("~0", "~")
                    actual_sources.add(ledger["supplemental_sources"][source_id]["source_type"])
            except (KeyError, IndexError, TypeError, ValueError):
                missing.append(f"{prefix}: unresolved source ref {ref!r}")
    limitations = evidence.get("limitations")
    if not isinstance(limitations, list) or any(not isinstance(item, str) or not substantive(item) for item in limitations):
        missing.append(f"{prefix}: limitations must be a list of specific reasons")
    elif evidence.get("judgment") == "无法确认" and not limitations:
        missing.append(f"{prefix}: unknown judgment needs a missing-evidence reason")
    if evidence.get("source_type") == "player_recollection" and evidence.get("judgment") == "数据明确显示":
        missing.append(f"{prefix}: player recollection is not parsed-data fact")
    if actual_sources and evidence.get("source_type") != "mixed" and actual_sources != {evidence.get("source_type")}:
        missing.append(f"{prefix}: source_type does not match referenced sources")
    if "player_recollection" in actual_sources and evidence.get("judgment") == "数据明确显示":
        missing.append(f"{prefix}: player recollection must remain distinguishable from data fact")
    return missing


def has_event_in_window(value: Any, start: float, end: float) -> bool:
    """Only explicit event times anchor a selected fight, not a generic player reference."""
    if isinstance(value, list):
        return any(has_event_in_window(item, start, end) for item in value)
    if not isinstance(value, dict):
        return False
    time = value.get("time")
    if type(time) in (int, float) and start <= time <= end:
        return True
    left, right = value.get("start"), value.get("end")
    return (type(left) in (int, float) and type(right) in (int, float)
            and left < right and max(start, left) < min(end, right))


def validate_markdown(review_text: str | None, match_id: Any) -> list[str]:
    if not isinstance(review_text, str) or not review_text.strip():
        return ["final gate requires --review-markdown with the delivered review"]
    missing = []
    if not re.search(r"(?<!\d)" + re.escape(str(match_id)) + r"(?!\d)", review_text):
        missing.append("review Markdown does not identify match_id")
    # Ignore headings inside code fences and reject heading-only placeholder documents.
    visible = re.sub(r"(?ms)^```[^\n]*\n.*?^```\s*$", "", review_text)
    headings = list(re.finditer(r"(?m)^#{1,6}\s+(.+)$", visible))
    previous = -1
    # Keep old completed reports readable; new reports start with a standalone global gameplan chapter.
    has_new_topic = any(title in heading.group(1) for title in SECTION_TITLES[1:] for heading in headings)
    legacy = not has_new_topic and any(LEGACY_SECTION_TITLES[0] in heading.group(1) for heading in headings)
    titles = LEGACY_SECTION_TITLES if legacy else SECTION_TITLES
    levels = set()
    for title in titles:
        candidates = [(i, match) for i, match in enumerate(headings) if title in match.group(1)]
        if candidates:
            # A subsection can repeat its parent's name after cooperative merging.
            outer_level = min(len(match.group(0).split()[0]) for _, match in candidates)
            candidates = [(i, match) for i, match in candidates
                          if len(match.group(0).split()[0]) == outer_level]
        if len(candidates) != 1:
            missing.append(f"review section missing or duplicated: {title}")
            continue
        index, heading = candidates[0]
        if index <= previous:
            missing.append(f"review section out of order: {title}")
        previous = index
        end = headings[index + 1].start() if index + 1 < len(headings) else len(visible)
        # Subheadings are allowed immediately after a section heading.
        level = len(heading.group(0).split()[0])
        levels.add(level)
        for later in headings[index + 1:]:
            if len(later.group(0).split()[0]) <= level:
                end = later.start()
                break
        else:
            end = len(visible)
        body = re.sub(r"(?m)^#{1,6}\s+.*$", "", visible[heading.end():end]).strip()
        if not substantive(body):
            missing.append(f"review section has no substantive body: {title}")
    if len(levels) > 1:
        missing.append("review topic headings must use the same heading level")
    return missing


FIGHT_TABLE_COLUMNS = ("阵营", "英雄", "是否参战", "关键技能与物品", "英雄伤害", "击杀/死亡记录")


def validate_fight_tables(text: Any, fight_ids: list[str] | None = None,
                          allow_empty: bool = False) -> list[str]:
    """Require per-fight data followed by prose; numbers and causality need semantic review."""
    if not isinstance(text, str):
        return ["fight report text is missing"]
    # Mask both kinds of fenced code while preserving offsets.
    lines = text.splitlines(keepends=True)
    fence = None
    for i, line in enumerate(lines):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        was_fenced = fence is not None
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[marker.end():].strip():
                fence = None
        if was_fenced or fence is not None:
            lines[i] = re.sub(r"[^\r\n]", " ", line)
    visible = "".join(lines)
    marks = list(re.finditer(r"(?m)^<!-- review-fight: ([^<>\r\n]+?) -->[ \t]*$", visible))
    ids = [m.group(1) for m in marks]
    errors = []
    if len(ids) != len(set(ids)):
        errors.append("fight table identifiers must be unique")
    if fight_ids is not None and (any(not isinstance(identifier, str) for identifier in fight_ids)
                                 or len(ids) != len(fight_ids) or set(ids) != set(fight_ids)):
        errors.append("fight tables must match every selected fight id exactly once")
    if not ids and not allow_empty:
        errors.append("each fight requires a review-fight marker, participant table and analysis")
    for i, mark in enumerate(marks):
        label = "fight " + mark.group(1)
        end = marks[i + 1].start() if i + 1 < len(marks) else len(visible)
        block = visible[mark.end():end]
        headings = list(re.finditer(r"(?m)^(#{1,6})\s+(.+)$", block))
        if not headings or block[:headings[0].start()].strip():
            errors.append(label + ": marker must precede a fight heading")
            continue
        first = headings[0]
        if not re.search(r"天辉\s*(?:\d+|未确认)\s*[:：]\s*(?:\d+|未确认)\s*夜魇", first.group(2)):
            errors.append(label + ": heading requires radiant:dire hero kill counts")
        level = len(first.group(1))
        # A table or summary from the next peer topic cannot satisfy this fight.
        boundary = next((h.start() for h in headings[1:] if len(h.group(1)) <= level), len(block))
        body = block[first.end():boundary].strip()
        rows = body.splitlines()
        if len(rows) < 3 or not rows[0].lstrip().startswith("|"):
            errors.append(label + ": participant table must precede analysis")
            continue
        def cells(line):
            return [value.strip() for value in line.strip().strip("|").split("|")]
        headers, separator = cells(rows[0]), cells(rows[1])
        if (not set(FIGHT_TABLE_COLUMNS) <= set(headers) or len(separator) != len(headers)
                or not all(re.fullmatch(r":?-{3,}:?", value) for value in separator)):
            errors.append(label + ": participant table columns or separator are invalid")
            continue
        if set(headers) & {"控制记录", "证据与限制", "参战依据", "经济/经验变化"}:
            errors.append(label + ": remove detailed evidence, control and economy columns")
        participation_index = headers.index("是否参战")
        row_count, tail = 0, 2
        while tail < len(rows) and rows[tail].lstrip().startswith("|"):
            values = cells(rows[tail])
            if (len(values) != len(headers)
                    or any(not substantive(value) for j, value in enumerate(values) if j != participation_index)):
                errors.append(label + ": participant row contains empty or invalid cells")
            if len(values) == len(headers) and values[participation_index] not in {"是", "否", "无法确认"}:
                errors.append(label + ": participation must be yes, no or unknown")
            row_count += 1
            tail += 1
        if row_count == 0:
            errors.append(label + ": participant table has no data rows")
        prose = re.sub(r"(?m)^#{1,6}\s+.*$|^<!--.*-->[ \t]*$|^\|.*$", "", "\n".join(rows[tail:])).strip()
        if not substantive(prose):
            errors.append(label + ": table must be followed by fight analysis and summary")
        after_table = "\n".join(rows[tail:])
        subheads = list(re.finditer(r"(?m)^(#{1,6})\s+(.+)$", after_table))
        positions = []
        for title in ("团战分析", "本人本波表现", "本波总结"):
            found = [h for h in subheads if h.group(2).strip() == title]
            if len(found) != 1:
                errors.append(label + ": requires one substantive " + title + " subsection")
                continue
            head = found[0]
            positions.append(head.start())
            stop = next((h.start() for h in subheads if h.start() > head.start()
                         and len(h.group(1)) <= len(head.group(1))), len(after_table))
            content = re.sub(r"(?m)^#{1,6}\s+.*$|^<!--.*-->[ \t]*$|^\|.*$", "", after_table[head.end():stop]).strip()
            if not substantive(content):
                errors.append(label + ": " + title + " has no substantive prose")
        if len(positions) == 3 and positions != sorted(positions):
            errors.append(label + ": fight analysis, personal performance and summary must appear in order")
    return errors


def validate_coverage(ledger: dict[str, Any], stage: str = "final", review_text: str | None = None) -> list[str]:
    """Structural/evidence-reference validation; not proof of tactical correctness."""
    if not isinstance(ledger, dict) or ledger.get("schema_version") != SCHEMA_VERSION:
        return ["schema_version must be 2; regenerate legacy ledgers from their source JSON"]
    if stage not in {"draft", "final"}:
        return ["stage must be draft or final"]
    raw = ledger.get("source_match")
    if not isinstance(raw, dict):
        return ["source_match is missing"]
    if ledger.get("source_sha256") != source_digest(raw):
        return ["source_match checksum mismatch; regenerate instead of editing source facts"]
    audit = parse_audit(raw, {})
    if not audit["strict_complete"]:
        return ["source data is not complete: " + ", ".join(audit["missing_for_full_review"])]
    canonical = build_ledger(raw, None, ledger.get("user_account_id"))
    missing = []
    for field in ("match", "players", "teamfights", "objectives", "roshan_aegis_lifecycles", "death_timeline"):
        if ledger.get(field) != canonical[field]:
            missing.append(f"{field} differs from extracted source facts")
    user_index = canonical["equipment_analysis_template"]["user_index"]
    if user_index is None:
        missing.append("user account is not identified in this match")
    supplementals = ledger.get("supplemental_sources", {})
    if not isinstance(supplementals, dict):
        return missing + ["supplemental_sources must be an object"]
    for key, source in supplementals.items():
        if not isinstance(source, dict):
            missing.append(f"supplemental_sources.{key} must be an object")
            continue
        require_fields(source, ("locator", "time_basis"), f"supplemental_sources.{key}", missing)
        if source.get("source_type") not in {"event_log", "replay", "player_recollection"}:
            missing.append(f"supplemental_sources.{key}: invalid source_type")
        if source.get("match_id") != raw["match_id"] or not nonempty(source.get("data")):
            missing.append(f"supplemental_sources.{key}: wrong match or empty data")
    analysis = ledger.get("analysis")
    if not isinstance(analysis, dict):
        return missing + ["analysis records are missing; counters do not count as analysis"]
    records = {}
    for category, fields in RECORD_FIELDS.items():
        rows = analysis.get(category)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            missing.append(f"analysis.{category} must be an array of objects")
            rows = []
        records[category] = rows
        seen = set()
        for index, row in enumerate(rows):
            prefix = f"analysis.{category}[{index}]"
            identifier = row.get("id")
            if not isinstance(identifier, str) or not substantive(identifier) or identifier in seen:
                missing.append(f"{prefix}: unique nonempty id required")
            else:
                seen.add(identifier)
            if category in {"cores", "supports"} and row.get("detail", "detailed") != "detailed":
                missing.append(f"{prefix}: full review requires detailed player analysis")
            require_fields(row, fields, prefix, missing)
            if category == "key_skills":
                validate_windows(row, ("high_value_window", "low_or_unrecorded_window"), prefix, missing)
            missing.extend(validate_evidence(row.get("evidence"), ledger, prefix))
            if category == "lanes":
                pre_lane = row.get("pre_lane")
                if not isinstance(pre_lane, dict):
                    missing.append(f"{prefix}.pre_lane: theoretical matchup object required")
                else:
                    require_fields(pre_lane, PRE_LANE_FIELDS, prefix + ".pre_lane", missing)
                    lane_evidence = pre_lane.get("evidence")
                    missing.extend(validate_evidence(lane_evidence, ledger, prefix + ".pre_lane"))
                    if (not isinstance(lane_evidence, dict) or lane_evidence.get("judgment")
                            not in {"模型判断", "经验估计", "无法确认"}):
                        missing.append(f"{prefix}.pre_lane: theory must be model judgment, estimate or explicit unknown")
            if category in {"global_gameplans", "decisive_fights"}:
                evidence = row.get("evidence")
                if not isinstance(evidence, dict) or evidence.get("judgment") not in {"模型判断", "高概率推断", "经验估计"}:
                    missing.append(f"{prefix}: gameplan must be labeled as model judgment, not player intent fact")
            if row.get("status") not in {"complete", "not_applicable"}:
                missing.append(f"{prefix}: limited or pending analysis cannot pass a full-review gate")
            if row.get("status") == "not_applicable" and not substantive(row.get("reason")):
                missing.append(f"{prefix}: not_applicable requires a reason")
    for category, expected in (("lanes", 3), ("support_lane_pressure", 4), ("cores", 6), ("supports", 4)):
        if len(records[category]) != expected:
            missing.append(f"analysis.{category}: requires {expected} actual records")
    if {row.get("id") for row in records["lanes"] if isinstance(row.get("id"), str)} != {"top", "mid", "bottom"}:
        missing.append("lanes must identify top, mid and bottom")
    def player_ids(category: str) -> list[int]:
        indices = [row.get("player_index") for row in records[category]]
        if any(type(i) is not int or not 0 <= i < 10 for i in indices) or len(set(str(i) for i in indices)) != len(indices):
            missing.append(f"analysis.{category}: unique valid player indices required")
        return [i for i in indices if type(i) is int and 0 <= i < 10]
    cores, supports, pressure = player_ids("cores"), player_ids("supports"), player_ids("support_lane_pressure")
    if set(cores) & set(supports) or set(cores + supports) != set(range(10)):
        missing.append("core/support audits must partition the ten players exactly once")
    if set(pressure) != set(supports):
        missing.append("support pressure must cover the same four support players")
    for side in ("radiant", "dire"):
        expected_slots = range(5) if side == "radiant" else range(128, 133)
        if sum(raw["players"][i]["player_slot"] in expected_slots for i in cores) != 3:
            missing.append(f"{side} needs three core-role audits; role assignments remain model judgments")
        skills = [row for row in records["key_skills"] if row.get("side") == side]
        if not 2 <= len(skills) <= 4:
            missing.append(f"key_skills.{side}: requires 2-4 records")
        identifiers = [row.get("ability") for row in skills]
        if any(not isinstance(i, str) or not substantive(i) for i in identifiers) or len(set(str(i) for i in identifiers)) != len(identifiers):
            missing.append(f"key_skills.{side}: unique ability names required")
    if any(row.get("side") not in {"radiant", "dire"} for row in records["key_skills"]):
        missing.append("key_skills has invalid side")
    plans = records["global_gameplans"]
    if not 2 <= len(plans) <= 3:
        missing.append("global_gameplans requires 2-3 actual records")
    plan_ids = {p["id"] for p in plans if isinstance(p.get("id"), str)}
    referenced_plans = set()
    fights = records["decisive_fights"]
    for row in fights:
        if type(row.get("start")) not in (int, float) or type(row.get("end")) not in (int, float) or not -120 <= row["start"] < row["end"] <= raw["duration"]:
            missing.append(f"fight {row.get('id')}: valid match time window required")
        else:
            anchors = []
            refs = (row.get("evidence") or {}).get("refs", []) if isinstance(row.get("evidence"), dict) else []
            for ref in refs if isinstance(refs, list) else []:
                try:
                    if isinstance(ref, str) and ref.startswith("/supplemental_sources/"):
                        source_id = ref.split("/")[2].replace("~1", "/").replace("~0", "~")
                        source = supplementals[source_id]
                        if source.get("source_type") not in {"event_log", "replay"} or source.get("time_basis") != "game_seconds":
                            continue
                    anchors.append(resolve_pointer(ledger, ref))
                except (KeyError, IndexError, TypeError, ValueError):
                    pass
            if not any(has_event_in_window(value, row["start"], row["end"]) for value in anchors):
                missing.append(f"fight {row.get('id')}: source reference must anchor an event in the selected time window")
        links = row.get("global_gameplan_ids")
        if not isinstance(links, list) or not links or any(not isinstance(link, str) or link not in plan_ids for link in links):
            missing.append(f"fight {row.get('id')}: valid global_gameplan_ids required")
        else:
            referenced_plans.update(links)
        if not isinstance(row.get("core_question"), str) or not substantive(row.get("core_question")):
            missing.append(f"fight {row.get('id')}: exactly one core_question string required")
    if len({(str(row.get('start')), str(row.get('end'))) for row in fights}) != len(fights):
        missing.append("decisive_fights contains duplicate time windows")
    if not 3 <= len(fights) <= 5:
        selection = analysis.get("fight_selection")
        if not isinstance(selection, dict):
            missing.append("fight count outside 3-5 needs a documented selection exception")
        else:
            require_fields(selection, ("reason", "timeline_crosscheck"), "fight_selection", missing)
            missing.extend(validate_evidence(selection.get("evidence"), ledger, "fight_selection"))
    if fights and referenced_plans != plan_ids:
        missing.append("each global gameplan must be tested by at least one selected fight")
    if user_index is not None:
        death_count = raw["players"][user_index].get("deaths")
        if type(death_count) is not int or len(records["user_deaths"]) != death_count:
            missing.append("user_deaths actual record count must match the user's source death count")
    resource_ids = [row.get("id") for row in records["resource_categories"]]
    if set(str(i) for i in resource_ids) != set(RESOURCE_CATEGORIES) or len(resource_ids) != 4:
        missing.append("resource_categories must cover all four fixed categories")
    missing.extend(validate_training(records["training"], ledger, "analysis.training"))
    # Recompute mandatory equipment subjects from source data, ignoring editable requirement lists.
    equipment = ledger.get("equipment_analysis_template")
    if not isinstance(equipment, dict):
        return missing + ["equipment_analysis_template is missing"]
    checked = copy.deepcopy(ledger)
    checked_equipment = checked["equipment_analysis_template"]
    for key in ("required_player_indices", "required_bkb_player_indices", "user_index"):
        checked_equipment[key] = canonical["equipment_analysis_template"][key]
    checked["roshan_aegis_lifecycles"] = canonical["roshan_aegis_lifecycles"]
    missing.extend(validate_equipment_analysis(checked))
    team_plan = equipment.get("bkb_team_plan")
    if not isinstance(team_plan, dict):
        missing.append("bkb_team_plan: both teams require core BKB allocation analysis")
    else:
        for side in ("radiant", "dire"):
            prefix = f"bkb_team_plan.{side}"
            plan = team_plan.get(side)
            if not isinstance(plan, dict):
                missing.append(f"{prefix}: allocation object required")
                continue
            require_fields(plan, ("allocation", "tradeoff"), prefix, missing)
            plan_evidence = plan.get("evidence")
            missing.extend(validate_evidence(plan_evidence, ledger, prefix))
            if (not isinstance(plan_evidence, dict) or plan_evidence.get("judgment")
                    not in {"模型判断", "经验估计", "无法确认"}):
                missing.append(f"{prefix}: allocation must be model judgment, estimate or explicit unknown")
    for key in ("player_item_audits", "bkb_player_audits", "aegis_lifecycle_audits"):
        for index, row in enumerate(equipment.get(key) or []):
            if isinstance(row, dict):
                prefix = f"{key}[{index}]"
                missing.extend(validate_evidence(row.get("evidence"), ledger, prefix))
                if key == "player_item_audits":
                    expected_role = "core" if row.get("player_index") in cores else "support"
                    if row.get("role") not in {expected_role, "核心" if expected_role == "core" else "辅助"}:
                        missing.append(f"{prefix}: role conflicts with player analysis")
                    for item in row.get("key_items") or []:
                        if isinstance(item, dict):
                            require_fields(item, ("component_flow", "prior_fight", "team_enabling"), prefix, missing)
                            missing.extend(validate_evidence(item.get("evidence"), ledger, prefix + ".key_item"))
    if user_index is not None:
        missing.extend(validate_evidence((equipment.get("user_build_path") or {}).get("evidence"), ledger, "user_build_path"))
    missing.extend(validate_evidence(equipment.get("high_risk_items_buyback_evidence"), ledger, "high_risk_items_buyback"))
    if stage == "final":
        missing.extend(validate_markdown(review_text, raw["match_id"]))
        missing.extend(validate_fight_tables(review_text, [row.get("id") for row in fights],
                                             allow_empty=not fights))
    return missing


def build_ledger(match: dict[str, Any], recent_raw: Any, user_account_id: Any) -> dict[str, Any]:
    players = match.get("players") or []
    recent_status = find_recent_status(recent_raw, match.get("match_id")) if recent_raw is not None else {}
    aegis_facts = roshan_aegis_lifecycles(match, players)
    return {
        "schema_version": SCHEMA_VERSION,
        "user_account_id": user_account_id,
        "source_match": copy.deepcopy(match),
        "source_sha256": source_digest(match),
        "supplemental_sources": {},
        "analysis": analysis_template(),
        "match": {
            "match_id": match.get("match_id"),
            "start_time": match.get("start_time"),
            "duration": match.get("duration"),
            "radiant_win": match.get("radiant_win"),
            "patch": match.get("patch"),
            "version": match.get("version"),
            "radiant_score": match.get("radiant_score"),
            "dire_score": match.get("dire_score"),
        },
        "parse_audit": parse_audit(match, recent_status),
        "players": [player_fact(player, index) for index, player in enumerate(players)],
        "death_timeline": death_timeline(players),
        "advantage_swings": advantage_swings(match),
        "objectives": match.get("objectives") or [],
        "roshan_aegis_lifecycles": aegis_facts,
        "teamfights": teamfight_facts(match, players),
        "equipment_analysis_template": equipment_analysis_template(match, user_account_id, aegis_facts),
        "analysis_coverage_template": coverage_template(match, user_account_id),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("match_json", type=Path, nargs="?")
    parser.add_argument("--recent-json", type=Path, help="Optional recent-match list for status conflict checks")
    parser.add_argument("--user-account-id", help="Used to set the required user-death count")
    parser.add_argument("--output", type=Path, help="Write the ledger to a file instead of stdout")
    parser.add_argument("--check-ledger", type=Path, help="Validate a populated ledger's mandatory coverage and exit")
    parser.add_argument("--stage", choices=("draft", "final"), default="final", help="Draft readiness or final delivery gate")
    parser.add_argument("--review-markdown", type=Path, help="Required review artifact for the final gate")
    args = parser.parse_args()

    try:
        if args.check_ledger:
            ledger = load_json(args.check_ledger)
            review_text = args.review_markdown.read_text(encoding="utf-8") if args.review_markdown else None
            missing = validate_coverage(ledger, stage=args.stage, review_text=review_text)
            analysis = ledger.get("analysis", {}) if isinstance(ledger, dict) else {}
            counts = {key: len(analysis.get(key, [])) for key in RECORD_FIELDS
                      if isinstance(analysis, dict) and isinstance(analysis.get(key), list)}
            print(json.dumps({"passed": not missing, "stage": args.stage,
                              "allowed_status": ("DRAFT_ALLOWED" if args.stage == "draft" else "REVIEW_COMPLETE") if not missing else "NOT_ALLOWED",
                              "record_counts": counts, "missing": missing,
                              "scope": "Structural and reference checks only; semantic evidence review remains required."},
                             ensure_ascii=False, indent=2))
            return 0 if not missing else 3
        if args.match_json is None:
            parser.error("match_json is required unless --check-ledger is used")
        match = unwrap_match(load_json(args.match_json))
        recent_raw = load_json(args.recent_json) if args.recent_json else None
        ledger = build_ledger(match, recent_raw, args.user_account_id)
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        print(f"fact-ledger error: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(ledger, ensure_ascii=False, indent=2)
    try:
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(rendered + "\n")
        else:
            print(rendered)
    except OSError as exc:
        print(f"fact-ledger error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
