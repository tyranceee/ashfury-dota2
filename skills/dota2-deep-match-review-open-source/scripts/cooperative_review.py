#!/usr/bin/env python3
"""Prepare compact evidence, validate a cooperative audit, and assemble reviewed sections.
No model calls or network access. This gate checks structure/provenance, not tactical truth.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
import extract_match_facts as facts

SCHEMA = "dota.cooperative-review.v1"
SECTIONS = ("gameplan", "summary", "points", "lanes", "tempo", "skills", "deaths",
            "equipment", "fights", "contribution", "training")
BASE_SECTIONS = tuple(key for key in SECTIONS if key != "gameplan")
REWRITE = {"gameplan", "summary", "equipment", "fights", "contribution", "training"}
TOPIC_GROUPS = (
    ("gameplan",), ("summary", "points"), ("lanes",), ("tempo", "fights"),
    ("equipment", "skills"), ("contribution", "deaths"), ("training",),
)
CHECKS = ("intake", "independent_scan", "important_claims", "global_gameplans")
SECTION_NAMES = dict(zip(SECTIONS, (
    "全局博弈", "一句话结论", "本场要点", "三路对线", "节奏与地图转化", "关键技能",
    "用户死亡", "装备", "团战", "贡献", "本人对局改进意见",
)))
REVIEW_NAMES = {"sampled": "GPT 抽查采用", "verified": "GPT 核验采用", "rewritten": "GPT 重写"}
MARKER = re.compile(r"^<!-- review-section: ([a-z_]+) -->\s*$", re.M)

def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def sections(text):
    # Fenced code cannot introduce real section boundaries.
    visible = re.sub(r"(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1\s*$",
                     lambda m: " " * len(m.group(0)), text)
    marks = list(MARKER.finditer(visible))
    result = {}
    for i, mark in enumerate(marks):
        key = mark.group(1)
        if key not in SECTIONS + ("handoff",) or key in result:
            raise ValueError("unknown or duplicate section: " + key)
        end = marks[i+1].start() if i+1 < len(marks) else len(text)
        body = text[mark.end():end].strip()
        prose = re.sub(r"(?m)^#{1,6}\s+.*$", "", body).strip()
        if not facts.substantive(prose):
            raise ValueError("section has no substantive content: " + key)
        result[key] = body
    return result

def compact_index(match, account):
    players = []
    for i, player in enumerate(match.get("players", [])):
        row = {key: player.get(key) for key in
               ("player_slot", "hero_id", "deaths", "lane", "lane_role", "net_worth")}
        row["player_index"] = i
        row["is_user"] = str(player.get("account_id")) == str(account)
        row["ref"] = f"/source_match/players/{i}"
        row["curve_sample_basis"] = "Keys are array indices, not verified minute timestamps; inspect times before interpreting."
        row["sample_times"] = {
            str(t): player["times"][t] for t in (0, 5, 6, 10, 15, 20, 25, 30, 40, 50, 60)
            if isinstance(player.get("times"), list) and t < len(player["times"])
        }
        row["curve_samples"] = {
            key: {str(t): values[t] for t in (0, 5, 6, 10, 15, 20, 25, 30, 40, 50, 60)
                  if t < len(values)}
            for key in ("gold_t", "xp_t", "lh_t", "hero_damage_t")
            if isinstance(values := player.get(key), list)
        }
        row["minute_samples"] = {
            minute: values for minute, values in facts.minute_points(player).items()
            if int(minute) in (0, 5, 6, 10, 15, 20, 25, 30, 40, 50, 60)
            and any(value is not None for value in values.values())
        }
        row["minute_sample_basis"] = "Exact times entries in game seconds; null is unavailable, not zero."
        row["event_index"] = {
            key: [{**{k: event.get(k) for k in ("time", "key") if k in event},
                   "ref": f"/source_match/players/{i}/{key}/{n}"}
                  for n, event in enumerate(player.get(key, [])) if isinstance(event, dict)]
            for key in ("kills_log", "deaths_log", "buyback_log", "purchase_log")
            if isinstance(player.get(key), list)
        }
        players.append(row)
    return {
        "scope": "Deterministic index, not complete micro-events or model judgments; absent is not zero.",
        "match": {key: match.get(key) for key in
                  ("match_id", "patch", "version", "start_time", "duration", "radiant_win")},
        "parse_audit": facts.parse_audit(match, {}),
        "players": players,
        "team_curves": {key: match.get(key) for key in ("radiant_gold_adv", "radiant_xp_adv")},
        "objectives": match.get("objectives"),
        "fight_index": [{"start": fight.get("start"), "end": fight.get("end"),
                         "ref": f"/source_match/teamfights/{i}"}
                        for i, fight in enumerate(match.get("teamfights") or [])
                        if isinstance(fight, dict)],
    }

def source_bundle(audit):
    match = facts.unwrap_match(read_json(audit["source_path"]))
    if facts.source_digest(match) != audit.get("source_sha256"):
        raise ValueError("source changed; regenerate index and revisit affected checks")
    if match.get("match_id") != audit.get("match_id"):
        raise ValueError("source match_id mismatch")
    base = Path(audit["preliminary_path"]).read_text(encoding="utf-8")
    if digest(base) != audit.get("preliminary_sha256"):
        raise ValueError("preliminary changed; rebuild section mapping and audit")
    return match, base

def prepare(match_path, preliminary_path, account):
    match = facts.unwrap_match(read_json(match_path))
    base = Path(preliminary_path).read_text(encoding="utf-8")
    declared = re.findall(r"\bmatch_id\s*=\s*(\d+)", base)
    if any(int(value) != match.get("match_id") for value in declared):
        raise ValueError("preliminary declares a different match")
    parts = sections(base)
    if set(BASE_SECTIONS) - set(parts):
        raise ValueError("base lacks stable sections; normalize a copy or use standalone mode")
    return {
        "schema_version": SCHEMA, "mode": "cooperative", "match_id": match["match_id"],
        "user_account_id": account, "source_path": str(Path(match_path).resolve()),
        "source_sha256": facts.source_digest(match),
        "preliminary_path": str(Path(preliminary_path).resolve()),
        "preliminary_sha256": digest(base),
        "intake_note": "PENDING",
        "index": compact_index(match, account),
        "supplemental_sources": {},
        "checks": {key: {"note": "PENDING", "evidence": {}} for key in CHECKS},
        "sections": {key: {"action": "rewrite" if key in REWRITE else "pending",
                            "review": "pending", "note": "PENDING", "evidence": {}}
                     for key in SECTIONS},
        "claim_audits": [],
        "training_items": [],
        "final_semantic_check": {"status": "pending", "note": "PENDING"},
    }

def validate(audit, replacement_text):
    errors = []
    if audit.get("schema_version") != SCHEMA or audit.get("mode") != "cooperative":
        return ["wrong cooperative schema/mode"], None, None
    try:
        match, base = source_bundle(audit)
        original, replacement = sections(base), sections(replacement_text)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return [str(exc)], None, None
    if set(BASE_SECTIONS) - set(original):
        errors.append("base sections missing")
    # Rebuild; the model cannot edit the displayed index to hide original events.
    if audit.get("index") != compact_index(match, audit.get("user_account_id")):
        errors.append("deterministic index differs from source")
    if not facts.parse_audit(match, {})["strict_complete"]:
        errors.append("partial source cannot pass cooperative-complete gate")
    if not any(str(p.get("account_id")) == str(audit.get("user_account_id"))
               and p.get("account_id") is not None for p in match["players"]):
        errors.append("user not identified")
    supplementals = audit.get("supplemental_sources")
    if not isinstance(supplementals, dict):
        errors.append("supplemental_sources must be an object")
        supplementals = {}
    for key, value in supplementals.items():
        if (not isinstance(value, dict) or value.get("source_type") not in
                {"event_log", "replay", "player_recollection"} or
                value.get("match_id") != match["match_id"] or not value.get("data")):
            errors.append("invalid supplemental source: " + str(key))
            continue
        facts.require_fields(value, ("locator", "time_basis"), "supplemental." + key, errors)
    bundle = {"source_match": match, "supplemental_sources": supplementals}
    no_fights = audit.get("fight_selection")
    allow_no_fights = (isinstance(no_fights, dict)
                       and type(no_fights.get("selected_count")) is int
                       and no_fights["selected_count"] == 0)
    if allow_no_fights:
        facts.require_fields(no_fights, ("reason", "timeline_crosscheck"), "fight_selection", errors)
        errors.extend(facts.validate_evidence(no_fights.get("evidence"), bundle, "fight_selection"))
    errors.extend(facts.validate_fight_tables(replacement.get("fights", ""),
                                             fight_ids=[] if allow_no_fights else None,
                                             allow_empty=allow_no_fights))
    errors.extend(facts.validate_training(audit.get("training_items"), bundle, "training_items"))
    facts.require_fields(audit, ("intake_note",), "audit", errors)
    checks = audit.get("checks") or {}
    for key in CHECKS:
        record = checks.get(key) if isinstance(checks, dict) else None
        if not isinstance(record, dict):
            errors.append("missing check: " + key)
            continue
        facts.require_fields(record, ("note",), "checks." + key, errors)
        errors.extend(facts.validate_evidence(record.get("evidence"), bundle, "checks." + key))
    reviews = audit.get("sections")
    if not isinstance(reviews, dict) or set(reviews) != set(SECTIONS):
        return errors + ["section audit must identify all final sections exactly once"], None, None
    rewritten = set()
    for key, record in reviews.items():
        if not isinstance(record, dict):
            errors.append("invalid section audit: " + key)
            continue
        action, level = record.get("action"), record.get("review")
        if action == "rewrite":
            rewritten.add(key)
            if level != "rewritten":
                errors.append(key + ": replacement must be reviewed as rewritten")
        elif action == "adopt" and key not in REWRITE:
            if level not in {"sampled", "verified"}:
                errors.append(key + ": adoption needs sampling or verification")
        else:
            errors.append(key + ": invalid action or mandatory GPT section not rewritten")
        facts.require_fields(record, ("note",), key, errors)
        errors.extend(facts.validate_evidence(record.get("evidence"), bundle, key))
    if set(replacement) != rewritten:
        errors.append("replacement sections must exactly match rewrite decisions (no handoff)")
    claims = audit.get("claim_audits")
    if not isinstance(claims, list) or not claims:
        errors.append("important claims must be audited; a completion count is not enough")
        claims = []
    seen = set()
    for record in claims:
        if not isinstance(record, dict):
            errors.append("claim audit must be an object")
            continue
        key = record.get("id")
        if not isinstance(key, str) or not key or key in seen:
            errors.append("claim ids must be unique")
        seen.add(str(key))
        facts.require_fields(record, ("claim", "reason"), "claim." + str(key), errors)
        section = record.get("section")
        if section not in SECTIONS:
            errors.append("claim has invalid final section")
        verdict, handling = record.get("verdict"), record.get("handling")
        allowed = {"supported": {"retained", "replaced"},
                   "partial": {"qualified", "replaced", "removed"},
                   "unsupported": {"qualified", "removed"},
                   "refuted": {"replaced", "removed"}}
        if verdict not in allowed or handling not in allowed[verdict]:
            errors.append("claim verdict/handling conflict: " + str(key))
        if verdict != "supported" and section not in rewritten:
            errors.append("disputed claim left in adopted section: " + str(key))
        errors.extend(facts.validate_evidence(record.get("evidence"), bundle, "claim." + str(key)))
    return errors, original, replacement

def nest_section(body):
    """Nest a reviewed fragment under a topic without rewriting prose or fenced code."""
    lines = body.splitlines()
    headings = []
    fence = None
    for i, line in enumerate(lines):
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            run = marker.group(1)
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if fence is None and heading:
            headings.append((i, len(heading.group(1)), heading.group(2)))
    if headings:
        shift = max(0, 3 - min(level for _, level, _ in headings))
        for i, level, title in headings:
            if level + shift > 6:
                raise ValueError("section nesting exceeds Markdown depth; normalize a working copy")
            lines[i] = "#" * (level + shift) + " " + title
    return "\n".join(lines)


def assemble(audit, replacement_text):
    errors, original, replacement = validate(audit, replacement_text)
    if errors:
        raise ValueError("\n".join(errors))
    output = [f"# 比赛 {audit['match_id']} 协作精审复盘",
              "本报告由初步复盘底稿与 GPT 精审内容合并；审核范围见比赛结论与数据边界，不等于所有章节从零重做。"]
    rows = ["| 章节 | 本次处理 |", "| --- | --- |"] + [
        f"| {SECTION_NAMES[key]} | {REVIEW_NAMES[audit['sections'][key]['review']]} |"
        for key in SECTIONS
    ]
    for title, group in zip(facts.SECTION_TITLES, TOPIC_GROUPS):
        output.append("## " + title)
        for key in group:
            output.append(nest_section(replacement[key] if key in replacement else original[key]))
        if title == "比赛结论与数据边界":
            output.append("### 审核范围与限制\n\n初评已提供基础底稿；GPT 各章处理如下。抽查采用不等于逐项证实。\n\n" +
                          "\n".join(rows) + "\n\n" + audit["intake_note"])
    return "\n\n".join(output) + "\n"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("prepare")
    init.add_argument("match_json", type=Path)
    init.add_argument("--preliminary", type=Path, required=True)
    init.add_argument("--user-account-id", required=True)
    init.add_argument("--output", type=Path, required=True)
    get = sub.add_parser("get")
    get.add_argument("audit", type=Path)
    get.add_argument("--pointer", action="append", required=True)
    get.add_argument("--max-chars", type=int, default=30000)
    for name in ("check", "merge"):
        cmd = sub.add_parser(name)
        cmd.add_argument("audit", type=Path)
        cmd.add_argument("--replacements", type=Path, required=True)
        if name == "merge":
            cmd.add_argument("--output", type=Path, required=True)
        else:
            cmd.add_argument("--final", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.match_json, args.preliminary, args.user_account_id)
            with args.output.open("x", encoding="utf-8") as handle:
                json.dump(result, handle, ensure_ascii=False, indent=2)
            print(json.dumps({"status": "COOPERATIVE_DRAFT", "output": str(args.output)}))
            return 0
        audit = read_json(args.audit)
        if args.command == "get":
            match, _ = source_bundle(audit)
            bundle = {"source_match": match, "supplemental_sources": audit.get("supplemental_sources", {})}
            result = {ref: facts.resolve_pointer(bundle, ref) for ref in args.pointer}
            text = json.dumps(result, ensure_ascii=False, indent=2)
            if args.max_chars < 1 or len(text) > args.max_chars:
                raise ValueError("evidence response exceeds bound; choose narrower pointers, not truncated JSON")
            print(text)
            return 0
        replacement = args.replacements.read_text(encoding="utf-8")
        rendered = assemble(audit, replacement)
        if args.command == "merge":
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(rendered)
            print(json.dumps({"status": "COOPERATIVE_ASSEMBLED", "requires_semantic_review": True}))
        else:
            status = "COOPERATIVE_ASSEMBLY_READY"
            if args.final:
                if args.final.read_text(encoding="utf-8") != rendered:
                    raise ValueError("final report differs from audited assembly")
                semantic = audit.get("final_semantic_check") or {}
                if semantic.get("status") != "complete" or not facts.substantive(semantic.get("note")):
                    raise ValueError("final semantic check and specific note required")
                status = "COOPERATIVE_REVIEW_COMPLETE"
            print(json.dumps({"status": status, "scope": "structural checks; semantic review is reviewer-attested"}))
        return 0
    except (ValueError, OSError, KeyError, IndexError, TypeError, AttributeError) as exc:
        print(json.dumps({"status": "NOT_ALLOWED", "error": str(exc)}, ensure_ascii=False))
        return 3

if __name__ == "__main__":
    raise SystemExit(main())
