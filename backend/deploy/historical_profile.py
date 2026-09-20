"""Strict-cutoff historical player profiles built from basic match rows.

S20/L20, F10, and F5 are equal-weight windows. The selected match supplies only the
participant identity and cutoff; none of its performance fields enter scores,
tags, summaries, benchmarks, or role normalization.
"""

from collections import Counter
import math
import time


MODEL_VERSION = "ashfury-composition-v0.11-l-nonlinear-result"
MIN_SCORE_MATCHES = 10
MAX_SAMPLE_MATCHES = 20
DISPLAY_RISK_RAW_MIN = 30.0
DISPLAY_RISK_RAW_MAX = 70.0

PROJECT_FIELDS = (
    "match_id", "start_time", "duration", "lobby_type", "game_mode",
    "hero_id", "player_slot", "radiant_win", "kills", "deaths",
    "assists", "leaver_status", "party_size", "gold_per_min",
    "xp_per_min", "last_hits", "denies", "hero_damage", "tower_damage",
    "hero_healing", "level",
)


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _average(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _stddev(values):
    values = list(values)
    if not values:
        return 0.0
    mean = _average(values)
    return math.sqrt(_average((value - mean) ** 2 for value in values))


def _clamp(value, lower=0.0, upper=100.0):
    return max(lower, min(upper, value))


def _nonlinear_result_risk(win_rate):
    """Center result risk at 50%, with growing value per extra H20 win."""
    equivalent_wins = (_clamp(win_rate) - 50.0) / 5.0
    distance = abs(equivalent_wins)
    adjustment = 6.0 * distance + distance * distance
    if equivalent_wins >= 0:
        return _clamp(50.0 - adjustment)
    return _clamp(50.0 + adjustment)


def _round(value, digits=1):
    return round(float(value), digits)


def _percentile(value, distribution):
    """Mid-rank empirical percentile in the closed range 0..100."""
    if not distribution:
        return 50.0
    lower = sum(1 for item in distribution if item < value)
    equal = sum(1 for item in distribution if item == value)
    return 100.0 * (lower + equal / 2.0) / len(distribution)


def _display_risk(raw_score):
    """Expand the model's useful 30..70 raw range onto the public 0..100 scale."""
    span = DISPLAY_RISK_RAW_MAX - DISPLAY_RISK_RAW_MIN
    return _clamp((raw_score - DISPLAY_RISK_RAW_MIN) * 100.0 / span)


def _won(match):
    radiant = int(_number(match.get("player_slot"))) < 128
    return radiant == bool(match.get("radiant_win"))


def strict_history(matches, cutoff_match_id, cutoff_start_time, limit=MAX_SAMPLE_MATCHES):
    """Return ranked, completed, non-leaver matches strictly before cutoff."""
    eligible = []
    for match in matches if isinstance(matches, list) else []:
        match_id = int(_number(match.get("match_id")))
        start_time = int(_number(match.get("start_time")))
        duration = int(_number(match.get("duration")))
        if int(_number(match.get("lobby_type"), -1)) != 7:
            continue
        if int(_number(match.get("leaver_status"))) != 0:
            continue
        if match_id <= 0 or match_id >= int(cutoff_match_id):
            continue
        if start_time <= 0 or duration <= 0:
            continue
        if start_time + duration > int(cutoff_start_time):
            continue
        eligible.append(dict(match))
    eligible.sort(
        key=lambda item: (
            int(_number(item.get("start_time"))),
            int(_number(item.get("match_id"))),
        ),
        reverse=True,
    )
    return eligible[:limit]


def coarse_roles_by_slot(players):
    """Classify each team: top three last-hitters are core, bottom two support."""
    teams = {"radiant": [], "dire": []}
    for player in players if isinstance(players, list) else []:
        slot = int(_number(player.get("player_slot"), -1))
        if slot < 0:
            continue
        team = "radiant" if slot < 128 else "dire"
        teams[team].append({
            "player_slot": slot,
            "last_hits": _number(player.get("last_hits")),
        })

    roles = {}
    for rows in teams.values():
        if len(rows) != 5:
            continue
        # player_slot is a stable deterministic tie-breaker only; last_hits is
        # always the classification signal requested by the product owner.
        ranked = sorted(rows, key=lambda row: (-row["last_hits"], row["player_slot"]))
        for index, row in enumerate(ranked):
            roles[row["player_slot"]] = "core" if index < 3 else "support"
    return roles


def _features(account_id, match):
    duration_minutes = max(_number(match.get("duration")) / 60.0, 1.0)
    duration_tens = max(duration_minutes / 10.0, 0.1)
    return {
        "account_id": int(account_id),
        "match_id": int(_number(match.get("match_id"))),
        "hero_id": int(_number(match.get("hero_id"))),
        "role_group": match.get("role_group") if match.get("role_group") in {"core", "support"} else None,
        "win": 1.0 if _won(match) else 0.0,
        "kills": _number(match.get("kills")),
        "deaths": _number(match.get("deaths")),
        "assists": _number(match.get("assists")),
        "d10": _number(match.get("deaths")) / duration_tens,
        "ka10": (
            _number(match.get("kills")) + _number(match.get("assists"))
        ) / duration_tens,
        "gpm": _number(match.get("gold_per_min")),
        "xpm": _number(match.get("xp_per_min")),
        "lh10": _number(match.get("last_hits")) / duration_tens,
        "hdpm": _number(match.get("hero_damage")) / duration_minutes,
        "tdpm": _number(match.get("tower_damage")) / duration_minutes,
        "healpm": _number(match.get("hero_healing")) / duration_minutes,
    }


METRIC_KEYS = ("d10", "ka10", "gpm", "xpm", "lh10", "hdpm", "tdpm", "healpm")


def _metric_distributions(rows):
    return {key: [row[key] for row in rows] for key in METRIC_KEYS}


def _decorate_feature(row, distributions):
    row["death_risk"] = _percentile(row["d10"], distributions["d10"])
    row["resource"] = _average((
        _percentile(row["gpm"], distributions["gpm"]),
        _percentile(row["xpm"], distributions["xpm"]),
        _percentile(row["lh10"], distributions["lh10"]),
    ))
    utility = max(
        _percentile(row["tdpm"], distributions["tdpm"]),
        _percentile(row["healpm"], distributions["healpm"]),
    )
    row["impact"] = (
        0.30 * _percentile(row["ka10"], distributions["ka10"])
        + 0.35 * _percentile(row["hdpm"], distributions["hdpm"])
        + 0.20 * utility
        + 0.15 * (100.0 if row["win"] else 0.0)
    )
    row["inefficiency"] = _clamp(50.0 + row["resource"] - row["impact"])
    return row


def _confidence(sample_size, benchmark_players):
    if sample_size < MIN_SCORE_MATCHES:
        return "insufficient"
    if sample_size < 15 or benchmark_players < 4:
        return "low"
    if sample_size < 20 or benchmark_players < 7:
        return "medium_low"
    # Coarse core/support grouping is useful but cannot justify high confidence.
    return "medium"


def _tags(profile):
    scores = profile.get("scores") or {}
    if scores.get("L") is None:
        return ["历史数据不足"]
    components = profile["components"]
    composite_score = scores.get("C") if isinstance(scores.get("C"), (int, float)) else 0
    recent_risk = 100 - scores.get("F", 50)
    short_risk = 100 - scores.get("F5", 50)
    result = []
    if composite_score >= 75:
        result.append("重度含畜")
    elif composite_score >= 55:
        result.append("高危成分")
    elif composite_score <= 25:
        result.append("低风险成分")
    else:
        result.append("中等风险")

    if scores["L"] >= 65 and recent_risk >= 60:
        result.append("长期高危且近期红温")
    elif recent_risk >= 60 and short_risk >= 60:
        result.append("近期持续红温")
    elif recent_risk < 60 and short_risk >= 60:
        result.append("近5场突然红温")
    elif scores["L"] >= 65 and recent_risk < 40:
        result.append("历史高危近期收敛")
    elif scores["L"] < 40 and recent_risk >= 60:
        result.append("近期突然红温")
    elif scores["F"] >= 62:
        result.append("近期状态强")

    if components["death_risk"] >= 65:
        result.append("高死亡型")
    elif components["resource_inefficiency"] >= 65:
        result.append("吃资源低产出")
    elif components["volatility"] >= 65:
        result.append("高波动型")
    elif scores.get("S", 0) >= 65 and scores["L"] >= 55:
        result.append("高能高危")
    elif scores.get("S", 0) >= 60 and scores["L"] < 40:
        result.append("稳定型")
    elif profile["hero_pool"]["top"] and profile["hero_pool"]["top"][0]["games"] >= 10:
        result.append("绝活倾向")
    return result[:3]


def _summary(profile):
    scores = profile.get("scores") or {}
    if scores.get("L") is None:
        return "本场开始前可用的公开天梯历史不足10场，暂不生成含畜量判断。"
    l_text = "历史高危" if scores["L"] >= 65 else "历史风险偏低" if scores["L"] < 40 else "历史风险中等"
    f_text = "近期状态强" if scores["F"] >= 62 else "近期红温" if scores["F"] < 42 else "近期状态一般"
    f5_text = "短期状态强" if scores["F5"] >= 62 else "短期红温" if scores["F5"] < 42 else "短期状态一般"
    risk_tag = (profile.get("tags") or ["综合风险待定"])[0]
    trait = (profile.get("tags") or [None, "暂无显著单项风险"])[1] if len(profile.get("tags") or []) > 1 else "暂无显著单项风险"
    return (
        f"赛前20场{l_text}（L{scores['L']}、实力 S{scores['S']}），"
        f"最近10场{f_text}（F10 {scores['F']}），最近5场{f5_text}（F5 {scores['F5']}）；"
        f"历史含畜量 C{scores.get('C', '—')}，"
        f"判定为{risk_tag}，主要特征是{trait}。"
    )


def build_snapshot(
    histories_by_account,
    cutoff_match_id,
    cutoff_start_time,
    participant_meta=None,
    generated_at=None,
):
    participant_meta = participant_meta or {}
    generated_at = int(generated_at or time.time())
    histories = {}
    sample_rows = []
    for account_id, matches in histories_by_account.items():
        account_id = int(account_id)
        selected = strict_history(matches, cutoff_match_id, cutoff_start_time)
        histories[account_id] = selected
        if len(selected) >= MIN_SCORE_MATCHES:
            sample_rows.extend(
                _features(account_id, match)
                for match in selected
            )

    eligible_accounts = [
        account_id for account_id, rows in histories.items()
        if len(rows) >= MIN_SCORE_MATCHES
    ]
    benchmark_players = len(eligible_accounts)
    global_distributions = _metric_distributions(sample_rows)
    rows_by_role = {
        role_group: [row for row in sample_rows if row["role_group"] == role_group]
        for role_group in ("core", "support")
    }
    role_distributions = {
        role_group: _metric_distributions(rows)
        for role_group, rows in rows_by_role.items()
        if len(rows) >= MIN_SCORE_MATCHES
    }

    for row in sample_rows:
        distributions = role_distributions.get(
            row["role_group"], global_distributions
        )
        _decorate_feature(row, distributions)

    rows_by_account = {
        account_id: [row for row in sample_rows if row["account_id"] == account_id]
        for account_id in eligible_accounts
    }
    aggregates = []
    for account_id in eligible_accounts:
        rows = rows_by_account[account_id]
        def weighted(field):
            # H20 is a stable baseline: every eligible historical match is equal.
            return _average(row[field] for row in rows)

        aggregates.append({
            "account_id": account_id,
            "win_rate": weighted("win") * 100.0,
            "death_risk": weighted("death_risk"),
            "resource": weighted("resource"),
            "impact": weighted("impact"),
            "resource_inefficiency": weighted("inefficiency"),
            "tail_rate": 100.0 * sum(1 for row in rows if row["death_risk"] >= 80.0) / len(rows),
            "volatility_raw": (
                0.5 * _stddev(row["d10"] for row in rows)
                + 0.5 * _stddev(row["impact"] for row in rows) / 20.0
            ),
            "recent_win_rate": _average(row["win"] for row in rows[:10]) * 100.0,
            "recent_death_risk": _average(row["death_risk"] for row in rows[:10]),
            "recent_impact": _average(row["impact"] for row in rows[:10]),
            "recent_inefficiency": _average(row["inefficiency"] for row in rows[:10]),
            "recent_volatility_raw": _stddev(
                row["impact"] for row in rows[:10]
            ) / 20.0,
            "short_win_rate": _average(row["win"] for row in rows[:5]) * 100.0,
            "short_death_risk": _average(row["death_risk"] for row in rows[:5]),
            "short_impact": _average(row["impact"] for row in rows[:5]),
            "short_inefficiency": _average(row["inefficiency"] for row in rows[:5]),
            "short_volatility_raw": _stddev(
                row["impact"] for row in rows[:5]
            ) / 20.0,
        })

    tail_distribution = [item["tail_rate"] for item in aggregates]
    volatility_distribution = [item["volatility_raw"] for item in aggregates]
    recent_volatility_distribution = [item["recent_volatility_raw"] for item in aggregates]
    short_volatility_distribution = [item["short_volatility_raw"] for item in aggregates]
    aggregate_by_account = {}
    for item in aggregates:
        item["volatility"] = (
            0.55 * _percentile(item["tail_rate"], tail_distribution)
            + 0.45 * _percentile(item["volatility_raw"], volatility_distribution)
        )
        item["loss_rate"] = 100.0 - item["win_rate"]
        item["result_risk"] = _nonlinear_result_risk(item["win_rate"])
        item["low_impact"] = 100.0 - item["impact"]
        item["L"] = _clamp(
            0.50 * item["result_risk"]
            + 0.05 * item["death_risk"]
            + 0.30 * item["resource_inefficiency"]
            + 0.15 * item["low_impact"]
        )
        item["recent_volatility"] = _percentile(
            item["recent_volatility_raw"], recent_volatility_distribution
        )
        item["F"] = _clamp(
            0.40 * item["recent_win_rate"]
            + 0.30 * item["recent_impact"]
            + 0.20 * (100.0 - item["recent_inefficiency"])
            + 0.10 * (100.0 - item["recent_volatility"])
        )
        item["short_volatility"] = _percentile(
            item["short_volatility_raw"], short_volatility_distribution
        )
        item["F5"] = _clamp(
            0.40 * item["short_win_rate"]
            + 0.30 * item["short_impact"]
            + 0.20 * (100.0 - item["short_inefficiency"])
            + 0.10 * (100.0 - item["short_volatility"])
        )
        item["S"] = _clamp(
            0.25 * item["win_rate"]
            + 0.30 * item["impact"]
            + 0.20 * (100.0 - item["resource_inefficiency"])
            + 0.15 * (100.0 - item["death_risk"])
            + 0.10 * (100.0 - item["volatility"])
        )
        aggregate_by_account[item["account_id"]] = item

    profiles = []
    for account_id, matches in histories.items():
        meta = dict(participant_meta.get(account_id, {}))
        # The selected match's estimated position is metadata, not history.
        meta.pop("position_est", None)
        feature_rows = rows_by_account.get(account_id, [])
        role_counts = Counter(
            row["role_group"] for row in feature_rows if row["role_group"]
        )
        primary_role = role_counts.most_common(1)[0][0] if role_counts else None
        hero_counts = Counter(int(_number(match.get("hero_id"))) for match in matches)
        profile = {
            "account_id": account_id,
            **meta,
            "model_version": MODEL_VERSION,
            "sample_size": len(matches),
            "sample_match_ids": [int(_number(match.get("match_id"))) for match in matches],
            "confidence": _confidence(len(matches), benchmark_players),
            "role": {
                "status": "coarse_estimate" if primary_role else "unavailable",
                "normalized": bool(role_counts) and all(role in role_distributions for role in role_counts),
                "source": "team_last_hits_top3" if primary_role else None,
                "internal_only": True,
                "primary_group": primary_role,
                "games_by_group": dict(role_counts),
                "reason": (
                    "Each historical match classifies the team's top three last-hitters as core and bottom two as support"
                    if primary_role
                    else "Historical team context is unavailable; global benchmark used"
                ),
            },
            "hero_pool": {
                "unique_heroes": len(hero_counts),
                "top": [
                    {"hero_id": hero_id, "games": games}
                    for hero_id, games in hero_counts.most_common(5)
                ],
            },
        }
        aggregate = aggregate_by_account.get(account_id)
        if aggregate is None:
            profile.update({
                "scores": {
                    "S": None,
                    "L": None,
                    "F": None,
                    "F5": None,
                    "C": None,
                },
                "components": None,
                "windows": None,
            })
            profile["tags"] = _tags(profile)
            profile["summary"] = _summary(profile)
            profiles.append(profile)
            continue

        raw_matches = matches
        recent_5 = raw_matches[:5]
        recent_10 = raw_matches[:10]

        def raw_window(rows):
            return {
                "matches": len(rows),
                "wins": sum(1 for match in rows if _won(match)),
                "win_rate": _round(100.0 * sum(1 for match in rows if _won(match)) / len(rows)),
                "kills": _round(_average(_number(match.get("kills")) for match in rows)),
                "deaths": _round(_average(_number(match.get("deaths")) for match in rows)),
                "assists": _round(_average(_number(match.get("assists")) for match in rows)),
                "gpm": _round(_average(_number(match.get("gold_per_min")) for match in rows)),
                "xpm": _round(_average(_number(match.get("xp_per_min")) for match in rows)),
            }

        raw_composite_risk = _clamp(
            0.30 * aggregate["L"]
            + 0.50 * (100.0 - aggregate["F"])
            + 0.20 * (100.0 - aggregate["F5"])
        )
        composite_risk = _display_risk(raw_composite_risk)
        profile.update({
            "scores": {
                "S": int(round(aggregate["S"])),
                "L": int(round(aggregate["L"])),
                "F": int(round(aggregate["F"])),
                "F5": int(round(aggregate["F5"])),
                "C": int(round(composite_risk)),
            },
            "components": {
                "raw_composite_risk": _round(raw_composite_risk),
                "death_risk": _round(aggregate["death_risk"]),
                "resource_inefficiency": _round(aggregate["resource_inefficiency"]),
                "volatility": _round(aggregate["volatility"]),
                "loss_rate": _round(aggregate["loss_rate"]),
                "result_risk": _round(aggregate["result_risk"]),
                "low_impact": _round(aggregate["low_impact"]),
                "impact": _round(aggregate["impact"]),
                "resource": _round(aggregate["resource"]),
                "recent_death_risk": _round(aggregate["recent_death_risk"]),
                "recent_resource_inefficiency": _round(aggregate["recent_inefficiency"]),
                "recent_impact": _round(aggregate["recent_impact"]),
                "recent_volatility": _round(aggregate["recent_volatility"]),
                "short_death_risk": _round(aggregate["short_death_risk"]),
                "short_resource_inefficiency": _round(aggregate["short_inefficiency"]),
                "short_impact": _round(aggregate["short_impact"]),
                "short_volatility": _round(aggregate["short_volatility"]),
            },
            "windows": {
                "last_20": raw_window(raw_matches),
                "last_10": raw_window(recent_10),
                "last_5": raw_window(recent_5),
            },
            "audit": {
                "two_digit_death_games": sum(
                    1 for match in raw_matches if _number(match.get("deaths")) >= 10
                ),
                "top_quintile_death_risk_games": sum(
                    1 for row in feature_rows if row["death_risk"] >= 80.0
                ),
            },
        })
        profile["tags"] = _tags(profile)
        profile["summary"] = _summary(profile)
        profiles.append(profile)

    return {
        "schema_version": "ashfury.composition-profile.v0.11",
        "model_version": MODEL_VERSION,
        "generated_at": generated_at,
        "cutoff": {
            "match_id": int(cutoff_match_id),
            "start_time": int(cutoff_start_time),
            "strict": True,
            "target_match_excluded": True,
        },
        "data_scope": {
            "source": "opendota_player_matches_basic",
            "replay_parse_used_for_history": False,
            "assigned_role_used": False,
            "position_est_used_for_normalization": False,
            "role_normalized": bool(role_distributions),
            "coarse_role_source": "opendota_basic_match_team_last_hits",
            "coarse_role_rule": "top_3_last_hits_core_bottom_2_support_per_team",
            "historical_sample_excludes_current_match": True,
            "current_match_performance_used": False,
            "current_match_position_used": False,
            "model_note": "S20/L20/F10/F5 use only historical rows before cutoff. Metrics are normalized within core/support cohorts when team last-hit context is cached; no 1-5 role is inferred.",
        },
        "composition_weights": {
            "historical_risk_L20": 0.30,
            "recent_risk_from_F10": 0.50,
            "short_term_risk_from_F5": 0.20,
        },
        "historical_risk_weights": {
            "nonlinear_result_risk": 0.50,
            "death_risk": 0.05,
            "resource_inefficiency": 0.30,
            "volatility": 0.00,
            "low_impact": 0.15,
        },
        "historical_result_curve": {
            "baseline_win_rate": 50,
            "equivalent_window_matches": 20,
            "risk_at_10_wins": 50,
            "risk_at_11_wins": 43,
            "risk_at_12_wins": 34,
            "risk_at_13_wins": 23,
            "risk_at_14_wins": 10,
        },
        "state_score_weights": {
            "win_rate": 0.40,
            "impact": 0.30,
            "resource_efficiency": 0.20,
            "impact_stability": 0.10,
            "death_safety": 0.0,
        },
        "benchmark": {
            "eligible_players": benchmark_players,
            "sample_matches": len(sample_rows),
            "history_weighting": "equal",
            "recent_weighting": "equal",
            "role_cohorts": sorted(role_distributions),
            "minimum_matches": MIN_SCORE_MATCHES,
            "display_risk_raw_range": [DISPLAY_RISK_RAW_MIN, DISPLAY_RISK_RAW_MAX],
        },
        "profiles": profiles,
    }
