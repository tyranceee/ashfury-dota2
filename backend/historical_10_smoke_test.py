#!/usr/bin/env python3
"""Server-side smoke test for ten pre-match ranked history samples.

This test reads OpenDota basic player-match rows only. It never requests replay
parsing and never reads parsed match details.
"""

import json
import time
from pathlib import Path

import httpx


ACCOUNT_ID = 212121467
BASE_DIR = Path("/opt/dota2-mcp")
OUTPUT_FILE = BASE_DIR / "test_results" / "historical_10_smoke_test.json"
ENDPOINT = "https://api.opendota.com/api/players/{}/matches".format(ACCOUNT_ID)

PROJECT_FIELDS = [
    "start_time",
    "hero_id",
    "kills",
    "deaths",
    "assists",
    "gold_per_min",
    "xp_per_min",
    "last_hits",
    "denies",
    "hero_damage",
    "tower_damage",
    "hero_healing",
    "level",
    "net_worth",
    "party_size",
    "leaver_status",
    "average_rank",
    "cluster",
    "version",
    "item_0",
    "item_1",
    "item_2",
    "item_3",
    "item_4",
    "item_5",
    "item_neutral",
    "hero_variant",
    "lane",
    "lane_role",
    "is_roaming",
]

CORE_FIELDS = [
    "match_id",
    "start_time",
    "duration",
    "lobby_type",
    "hero_id",
    "player_slot",
    "radiant_win",
    "kills",
    "deaths",
    "assists",
    "gold_per_min",
    "xp_per_min",
    "last_hits",
    "denies",
    "hero_damage",
    "tower_damage",
    "hero_healing",
    "level",
    "net_worth",
    "party_size",
    "leaver_status",
    "average_rank",
    "cluster",
    "version",
    "item_0",
    "item_1",
    "item_2",
    "item_3",
    "item_4",
    "item_5",
    "item_neutral",
    "hero_variant",
    "lane",
    "lane_role",
    "is_roaming",
]


def fetch_ranked_matches():
    params = [("limit", "50"), ("lobby_type", "7")]
    params.extend(("project", field) for field in PROJECT_FIELDS)
    response = httpx.get(
        ENDPOINT,
        params=params,
        timeout=30,
        follow_redirects=True,
        headers={"User-Agent": "Ashfury-Historical-Profile-Smoke-Test/1.0"},
    )
    response.raise_for_status()
    return response.json(), {
        "remaining_minute": response.headers.get("x-rate-limit-remaining-minute"),
        "remaining_day": response.headers.get("x-rate-limit-remaining-day"),
    }


def strict_history(matches, count=10):
    ranked = [item for item in matches if int(item.get("lobby_type", -1)) == 7]
    ranked.sort(
        key=lambda item: (int(item.get("start_time") or 0), int(item["match_id"])),
        reverse=True,
    )
    if len(ranked) < count + 1:
        raise RuntimeError("Not enough ranked matches to create a strict cutoff sample")

    target = ranked[0]
    target_start = int(target["start_time"])
    target_id = int(target["match_id"])
    history = []
    for item in ranked[1:]:
        match_id = int(item["match_id"])
        ended_at = int(item.get("start_time") or 0) + int(item.get("duration") or 0)
        if match_id < target_id and ended_at <= target_start:
            history.append(item)
        if len(history) == count:
            break

    if len(history) != count:
        raise RuntimeError("Strict cutoff produced fewer than ten matches")
    return target, history


def field_availability(history):
    result = {}
    for field in CORE_FIELDS:
        available = sum(item.get(field) is not None for item in history)
        result[field] = {
            "available": available,
            "total": len(history),
            "coverage": round(available / float(len(history)), 3),
        }
    return result


def dry_run_detection(history):
    chronological = sorted(history, key=lambda item: int(item["match_id"]))
    baseline = str(int(chronological[0]["match_id"]) - 1)
    discovered = []
    for item in chronological:
        candidate = str(item["match_id"])
        if int(candidate) > int(baseline):
            discovered.append(candidate)
            baseline = candidate
    return {
        "input_count": len(chronological),
        "discovered_count": len(discovered),
        "unique_count": len(set(discovered)),
        "simulated_parse_triggers": len(discovered),
        "real_parse_requests_sent": 0,
        "match_ids": discovered,
    }


def main():
    matches, rate_limit = fetch_ranked_matches()
    target, history = strict_history(matches)
    target_id = int(target["match_id"])
    target_start = int(target["start_time"])

    invariants = {
        "sample_count_is_10": len(history) == 10,
        "target_match_excluded": all(int(item["match_id"]) != target_id for item in history),
        "all_match_ids_before_target": all(
            int(item["match_id"]) < target_id for item in history
        ),
        "all_matches_ended_before_target": all(
            int(item["start_time"]) + int(item["duration"]) <= target_start
            for item in history
        ),
        "ranked_only": all(int(item["lobby_type"]) == 7 for item in history),
        "no_match_details_requested": True,
        "no_replay_parse_requested": True,
        "assigned_role_not_claimed": True,
        "opendota_lane_role_excluded_from_true_assigned_role": True,
    }
    detection = dry_run_detection(history)
    invariants["detector_found_each_match_once"] = (
        detection["discovered_count"] == 10 and detection["unique_count"] == 10
    )

    report = {
        "test": "historical_10_ranked_smoke_test",
        "generated_at": int(time.time()),
        "account_id": ACCOUNT_ID,
        "source": "opendota_player_matches_basic",
        "target_cutoff": {
            "match_id": target_id,
            "start_time": target_start,
        },
        "sample_count": len(history),
        "invariants": invariants,
        "all_invariants_passed": all(invariants.values()),
        "field_availability": field_availability(history),
        "detection_dry_run": detection,
        "rate_limit_after_test": rate_limit,
        "history": history,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(OUTPUT_FILE)

    print(json.dumps({
        "output": str(OUTPUT_FILE),
        "target_match_id": target_id,
        "history_match_ids": [item["match_id"] for item in history],
        "sample_count": len(history),
        "all_invariants_passed": report["all_invariants_passed"],
        "full_coverage_fields": sorted(
            field for field, value in report["field_availability"].items()
            if value["coverage"] == 1.0
        ),
        "missing_or_partial_fields": sorted(
            field for field, value in report["field_availability"].items()
            if value["coverage"] < 1.0
        ),
        "real_parse_requests_sent": 0,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
