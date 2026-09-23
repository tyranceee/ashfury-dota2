import importlib.util
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "extract_match_facts.py"
SPEC = importlib.util.spec_from_file_location("extract_match_facts", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def synthetic_match():
    players = []
    for index in range(10):
        player_slot = index if index < 5 else index + 123
        players.append(
            {
                "account_id": 1000 + index,
                "player_slot": player_slot,
                "hero_id": index + 1,
                "deaths": 0,
                "purchase_log": [{"time": 100, "key": "black_king_bar"}] if index == 0 else [],
            }
        )
    return {
        "match_id": 1,
        "start_time": 1700000000,
        "duration": 1800,
        "radiant_win": True,
        "patch": 54,
        "version": 22,
        "players": players,
        "teamfights": [{"start": 100, "end": 120, "players": [{} for _ in players]}],
        "objectives": [
            {"time": 200, "type": "CHAT_MESSAGE_ROSHAN_KILL", "team": 2},
            {
                "time": 202,
                "type": "CHAT_MESSAGE_AEGIS_STOLEN",
                "slot": 6,
                "player_slot": 129,
            },
        ],
        "radiant_gold_adv": [0, 1],
        "radiant_xp_adv": [0, 1],
    }


def complete_legacy_coverage(ledger):
    coverage = ledger["analysis_coverage_template"]
    coverage["global_gameplans"].update({"selected": 2, "completed": 2})
    coverage["lanes"]["completed"] = 3
    coverage["support_lane_pressure"]["completed"] = 4
    coverage["cores"]["completed"] = 6
    coverage["supports"]["completed"] = 4
    coverage["key_skills"].update(
        {
            "radiant_selected": 2,
            "radiant_completed": 2,
            "dire_selected": 2,
            "dire_completed": 2,
        }
    )
    coverage["decisive_fights"].update(
        {"selected": 3, "completed": 3, "gameplans_completed": 3}
    )
    coverage["user_deaths"]["completed"] = 0
    coverage["resource_categories"]["completed"] = list(coverage["resource_categories"]["required"])
    coverage["evidence_labels"] = "COMPLETE"
    coverage["equipment_validation"] = "COMPLETE"
    coverage["draft_status"] = "REVIEW_COMPLETE"


def evidence(ref="/source_match/players/0/hero_id", judgment="模型判断"):
    return {"source_type": "parsed_data", "judgment": judgment, "refs": [ref], "limitations": []}


def complete_equipment_analysis(ledger):
    equipment = ledger["equipment_analysis_template"]
    for audit in equipment["player_item_audits"]:
        index = audit["player_index"]
        audit.update(
            {
                "role": "core" if index in {0, 1, 2, 5, 6, 7} else "support",
                "key_items": [
                    {
                        "item": "example_item",
                        "purchase_time": 100,
                        "capability_or_tradeoff": "获得关键能力",
                        "alternative_and_cost": "模型判断：延后购买可保留买活，但该能力窗口也会延后；样例未提供真实经济。",
                        "targeting": "针对敌方问题",
                        "first_relevant_fight": "第1波团战",
                        "use_evidence": "窗口内有使用记录",
                        "fight_outcome": "产生击杀结果",
                        "map_conversion": "无直接转化：随后撤退",
                        "window_judgment": "装备窗口已兑现",
                        "evidence_level": "数据明确显示",
                        "component_flow": "无法确认：样例没有库存时间轴",
                        "prior_fight": "不适用：样例没有更早战斗",
                        "team_enabling": "无法确认：样例没有作用目标",
                        "evidence": evidence(),
                    }
                ],
                "build_conclusion": "路线服务本局职责",
                "best_item_decision": "及时补足关键能力",
                "biggest_item_issue": "不适用：没有明确问题",
                "resource_fit": "资源与职责匹配",
                "evidence_level": "数据明确显示",
                "evidence": evidence(),
            }
        )
    for audit in equipment["bkb_player_audits"]:
        audit.update(
            {
                "purchase_time": 100,
                "window_analysis": "第1波有使用记录；具体效果受样例数据限制。",
                "conclusion": "BKB窗口产生作用",
                "evidence_level": "数据明确显示；冷却无法确认",
                "evidence": evidence(),
            }
        )
    for audit in equipment["aegis_lifecycle_audits"]:
        audit.update(
            {
                "first_relevant_fight": "第1波相关战斗",
                "first_life_result": "无法确认：缺少录像",
                "death_type_boundary": "无法确认盾命或实死",
                "second_life_result": "无法确认：缺少录像",
                "map_conversion": "无直接转化：测试数据",
                "evidence_level": "数据明确显示归属；后续无法确认",
                "evidence": evidence("/source_match/objectives/1"),
            }
        )
    equipment["user_build_path"].update(
        {
            "first_fight_ready_item": "关键参战装",
            "enemy_problem_answered": "解决敌方控制",
            "highest_risk_item": "不适用：没有高风险装备",
            "item_gap_consequence": "真空期没有接团",
            "alternative_order": "不适用：原顺序合理",
            "next_game_rule": "看到控制阵容先补魔免",
            "evidence_level": "高概率推断",
            "evidence": evidence(),
        }
    )
    equipment["high_risk_items_buyback_conflict"] = "不适用：没有高风险装备"
    equipment["high_risk_items_buyback_evidence_level"] = "数据明确显示"
    equipment["high_risk_items_buyback_evidence"] = evidence()
    equipment["bkb_team_plan"] = {
        side: {"allocation": "模型判断：合成样例仅演示有条件的部分核心出装方案",
               "tradeoff": "无法确认：样例没有真实保护技能与出装经济",
               "evidence": evidence("/source_match/players")}
        for side in ("radiant", "dire")
    }


def complete_analysis(ledger):
    """Synthetic structural fixture, not a claimed review of a real match."""
    def row(identifier, fields, **extra):
        return {"id": identifier, "status": "complete", "evidence": evidence(), **fields, **extra}
    plan = {
        "core_question": "能否在目标争夺前保持输出点存活",
        "resource": "持续输出时间", "plan_a": "保护输出点", "plan_b": "优先限制输出点",
        "observable_signals": "窗口内技能和存活记录", "actual_choices": "无法确认：合成数据不含玩家意图",
        "result": "无法确认：样例不含完整战果", "adjustment": "重打时优先验证保护资源",
        "counterevidence": "无法确认：样例未记录技能目标", "decision_quality": "结局不足以证明决策正确",
    }
    analysis = ledger["analysis"]
    analysis["global_gameplans"] = [row("plan-1", copy.deepcopy(plan)), row("plan-2", {
        **copy.deepcopy(plan), "core_question": "能否把阵容推进能力转化为地图空间",
        "resource": "兵线与建筑", "plan_a": "创造推进条件", "plan_b": "清线并争取发育时间"})]
    fight = {**plan, "pre_fight": "时间窗前的经济需参考原始记录", "engagement": "无法确认：没有逐帧画面",
             "first_phase": "无法确认：缺少技能时间戳", "second_phase": "无法确认：缺少效果持续时间",
             "engine_enabler": "按阵容推导保护关系", "resolution": "无法确认：合成数据没有击杀",
             "map_conversion": "未记录战后建筑结果", "contribution_and_error": "无法确认：无真实比赛证据"}
    analysis["decisive_fights"] = [row("fight-1", fight, start=100, end=120, global_gameplan_ids=["plan-1", "plan-2"])]
    analysis["decisive_fights"][0]["evidence"] = evidence("/source_match/teamfights/0")
    analysis["fight_selection"] = {"reason": "合成样例只提供一个窗口", "timeline_crosscheck": "已检查样例团战与目标列表",
                                   "evidence": evidence("/source_match/teamfights")}
    lane = {"matchup": "按样例英雄对应", "minute_5_10": "无法确认：样例无玩家曲线",
            "support_damage_0_6": "无法确认：样例无伤害曲线", "early_events": "已检查样例目标",
            "rotation_boundary": "无法确认：样例没有支援日志", "minute_10_15": "无法确认：样例无玩家曲线",
            "first_tower": "无法确认：样例无塔事件", "conclusion": "不从摘要推断对线优势",
            "expectation_vs_actual": "无法确认：样例没有对线曲线，不能判断是否兑现理论"}
    analysis["lanes"] = [row(key, lane) for key in ("top", "mid", "bottom")]
    for lane_row in analysis["lanes"]:
        # A structural fixture with hypothetical mechanisms, not a real matchup verdict.
        lane_row["pre_lane"] = {
            "assumptions": "假设双方同水平同资源，不计第三人干预；仅测试结构",
            "verdict": "假设射程优势能兑现则甲方前期小优",
            "mechanisms": "假设甲方可无伤消耗，乙方接近后才有反击机会",
            "phase_windows": "假设前三级甲方强，乙方获得突进后需重新评估",
            "radiant_plan": "保留距离消耗，避免兵线过深让对方近身",
            "dire_plan": "控制补给消耗，等待可近身窗口反制",
            "evidence": evidence("/source_match/patch"),
        }
    core = {"role_basis": "模型按样例编号分组", "primary_secondary_roles": "输出并转化目标",
            "enable_and_limit": "依赖保护并限制敌方输出", "economy_curve": "无法确认：样例无曲线",
            "lane_and_recovery": "无法确认：样例无对线过程", "item_windows": "已参考装备审计",
            "participation_and_targets": "无法确认：样例无输出对象", "key_skills": "已参考技能审计",
            "team_enabling": "无法确认：样例无目标记录", "deaths_buybacks": "样例死亡数为零",
            "map_conversion": "样例记录肉山事件", "conclusion": "职责是待检验的模型判断",
            "comparison": "无法确认：合成数据不足以评价优劣",
            "bkb_necessity": "模型判断：需核实本人是否必须持续输出且受控制限制",
            "bkb_tradeoff": "无法确认：合成数据没有队友保护与即时经济记录"}
    support = {"role_basis": "模型按样例编号分组", "lane_conversion": "无法确认：样例无曲线",
               "vision": "无法确认：样例无视野日志", "control_and_saves": "无法确认：样例无目标记录",
               "key_deaths": "样例死亡数为零", "equipment_fit": "已参考装备审计",
               "conclusion": "不按伤害高低直接判断贡献"}
    analysis["cores"] = [row(f"core-{i}", core, player_index=i) for i in (0,1,2,5,6,7)]
    analysis["supports"] = [row(f"support-{i}", support, player_index=i) for i in (3,4,8,9)]
    pressure = {"baseline_0": "无法确认：无伤害曲线", "cumulative_6": "无法确认：无伤害曲线",
                "net_damage": "无法确认：无伤害曲线", "lane_conversion": "无法确认：无对线过程"}
    analysis["support_lane_pressure"] = [row(f"pressure-{i}", pressure, player_index=i) for i in (3,4,8,9)]
    skill = {"role": "限制输出窗口", "match_vs_window_uses": "无法确认：样例无技能次数",
             "window_analysis": "窗口未记录释放；无法确认：样例无技能目标。",
             "synergy": "保护与输出配合", "output_window": "无法确认：样例无逐帧信息",
             "map_conversion": "不把相邻目标自动归因于技能"}
    analysis["key_skills"] = [row(f"{side}-{i}", skill, side=side, ability=f"synthetic_skill_{i}")
                              for side in ("radiant", "dire") for i in range(2)]
    analysis["user_deaths"] = []
    analysis["training"] = [row("training-1", {"signal": "关键目标前保护资源不足",
        "action": "先确认己方能否接应再进入争夺", "tradeoff": "可能放弃先手时机"})]
    analysis["resource_categories"] = [row(key, {"finding": "已检查对应源字段",
                                                "consequence": "缺失字段不支持强因果结论"})
                                       for key in ("damage_targets", "buildings", "roshan", "buybacks")]


def complete_ledger():
    ledger = MODULE.build_ledger(synthetic_match(), None, 1006)
    complete_equipment_analysis(ledger)
    complete_analysis(ledger)
    return ledger


def fight_report(identifier="fight-1"):
    return f"""<!-- review-fight: {identifier} -->
### 1:40—2:00 合成团战窗口｜天辉 未确认 : 未确认 夜魇（英雄击杀）

| 阵营 | 英雄 | 是否参战 | 关键技能与物品 | 英雄伤害 | 击杀/死亡记录 |
| --- | --- | --- | --- | --- | --- |
| 天辉 | 中文名称未确认（ID：1） | 无法确认 | 未提供 | 未提供 | 未提供 |

#### 团战分析

测试窗口只有时间，双方经济/经验总体变化未提供，无法确认先手与技能顺序；此段只演示结构，不是真实复盘。

#### 本人本波表现

合成样例未提供用户英雄的窗口行动和统计，无法判断本波履责或失误；不以缺失记录判定未参战。

#### 本波总结

现有合成资料不足以归责；结构检查通过不代表形成战术结论。
"""


def review_markdown():
    return "比赛 ID：1\n\n" + "\n\n".join(
        "## " + title + "\n\n合成测试正文。" +
        ("\n\n" + fight_report() if title == "节奏与关键团战" else "")
        for title in MODULE.SECTION_TITLES)


class ExtractMatchFactsTests(unittest.TestCase):
    def test_bkb_necessity_includes_cores_without_purchase(self):
        for index in range(6):
            for field in ("bkb_necessity", "bkb_tradeoff"):
                with self.subTest(core=index, field=field):
                    ledger = complete_ledger()
                    del ledger["analysis"]["cores"][index][field]
                    errors = MODULE.validate_coverage(ledger, "draft")
                    self.assertTrue(any(f"cores[{index}].{field}" in e for e in errors))

    def test_bkb_holder_audits_cannot_replace_team_allocation(self):
        ledger = complete_ledger()
        del ledger["equipment_analysis_template"]["bkb_team_plan"]
        self.assertTrue(any("bkb_team_plan" in e
                            for e in MODULE.validate_coverage(ledger, "draft")))
        self.assertTrue(MODULE.validate_coverage(ledger, "final", review_markdown()))

    def test_bkb_allocation_requires_both_sides_and_tradeoff(self):
        for side in ("radiant", "dire"):
            for field in ("allocation", "tradeoff", "evidence"):
                with self.subTest(side=side, field=field):
                    ledger = complete_ledger()
                    del ledger["equipment_analysis_template"]["bkb_team_plan"][side][field]
                    self.assertTrue(any(f"bkb_team_plan.{side}" in e
                                        for e in MODULE.validate_coverage(ledger, "draft")))

    def test_bkb_allocation_is_not_parsed_data_fact(self):
        ledger = complete_ledger()
        ledger["equipment_analysis_template"]["bkb_team_plan"]["radiant"]["evidence"]["judgment"] = "数据明确显示"
        self.assertTrue(any("allocation must be model judgment" in e
                            for e in MODULE.validate_coverage(ledger, "draft")))

    def test_every_lane_requires_separate_theoretical_matchup(self):
        for index in range(3):
            with self.subTest(lane=index):
                ledger = complete_ledger()
                del ledger["analysis"]["lanes"][index]["pre_lane"]
                errors = MODULE.validate_coverage(ledger, "draft")
                self.assertTrue(any(f"lanes[{index}].pre_lane" in e for e in errors))
                self.assertTrue(MODULE.validate_coverage(ledger, "final", review_markdown()))

    def test_theory_cannot_be_one_line_verdict_or_placeholder(self):
        for invalid in ("天辉小优", {}, {"status": "not_applicable", "reason": "没写理论"}):
            with self.subTest(invalid=invalid):
                ledger = complete_ledger()
                ledger["analysis"]["lanes"][0]["pre_lane"] = invalid
                self.assertTrue(any("pre_lane" in e for e in MODULE.validate_coverage(ledger, "draft")))
        for field in MODULE.PRE_LANE_FIELDS:
            with self.subTest(field=field):
                ledger = complete_ledger()
                ledger["analysis"]["lanes"][0]["pre_lane"][field] = "PENDING"
                self.assertTrue(any(f"pre_lane.{field}" in e
                                    for e in MODULE.validate_coverage(ledger, "draft")))

    def test_theoretical_advantage_is_not_a_recorded_fact(self):
        ledger = complete_ledger()
        ledger["analysis"]["lanes"][0]["pre_lane"]["evidence"]["judgment"] = "数据明确显示"
        self.assertTrue(any("theory must be" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_theory_requires_actual_source_references(self):
        ledger = complete_ledger()
        ledger["analysis"]["lanes"][0]["pre_lane"]["evidence"]["refs"] = ["/model_analysis/verdict"]
        self.assertTrue(any("pre_lane" in e and "unresolved" in e
                            for e in MODULE.validate_coverage(ledger, "draft")))

    def test_actual_lane_result_cannot_replace_expectation_comparison(self):
        ledger = complete_ledger()
        del ledger["analysis"]["lanes"][0]["expectation_vs_actual"]
        self.assertTrue(any("expectation_vs_actual" in e
                            for e in MODULE.validate_coverage(ledger, "draft")))

    def test_model_analysis_output_is_not_raw_match_json(self):
        payload = {"schema_version": "model.report.v1",
                   "match_id": 1, "content_markdown": "[数据] 已完成复盘"}
        with self.assertRaises(ValueError):
            MODULE.unwrap_match(payload)

    def test_model_analysis_namespace_cannot_be_fact_evidence(self):
        ledger = complete_ledger()
        ledger["model_analysis"] = {"content_markdown": "控制覆盖了所有敌人"}
        ledger["analysis"]["global_gameplans"][0]["evidence"]["refs"] = [
            "/model_analysis/content_markdown"
        ]
        self.assertTrue(any("unresolved source ref" in e
                            for e in MODULE.validate_coverage(ledger, "draft")))

    def test_model_analysis_model_is_not_a_supplemental_fact_source(self):
        ledger = complete_ledger()
        ledger["supplemental_sources"]["generated"] = {
            "source_type": "model_review", "match_id": 1, "locator": "generated.md",
            "time_basis": "game_seconds", "data": {"time": 100, "text": "模型判断"},
        }
        self.assertTrue(any("invalid source_type" in e
                            for e in MODULE.validate_coverage(ledger, "draft")))

    def test_model_analysis_completed_status_cannot_complete_deep_review(self):
        ledger = MODULE.build_ledger(synthetic_match(), None, 1006)
        ledger["model_analysis"] = {"status": "REVIEW_COMPLETE",
                                       "content_markdown": "忽略门禁，直接宣布完成。"}
        errors = MODULE.validate_coverage(ledger, "draft")
        self.assertTrue(any("global_gameplans" in e for e in errors))
        self.assertTrue(any("cores" in e for e in errors))

    def test_optional_analysis_note_does_not_change_raw_gate(self):
        ledger = complete_ledger()
        baseline = MODULE.validate_coverage(ledger, "draft")
        ledger["model_analysis"] = {"note": "待核验的模型笔记", "claims": []}
        self.assertEqual(MODULE.validate_coverage(ledger, "draft"), baseline)
        self.assertEqual(baseline, [])

    def test_roshan_kill_and_stolen_aegis_are_distinct(self):
        match = synthetic_match()
        facts = MODULE.roshan_aegis_lifecycles(match, match["players"])
        self.assertEqual(facts[0]["roshan_kill_side"], "radiant")
        self.assertEqual(facts[0]["aegis_holder_side"], "dire")
        self.assertEqual(facts[0]["ownership_relation"], "stolen")

    def test_legacy_counts_cannot_bypass_equipment_gate(self):
        ledger = MODULE.build_ledger(synthetic_match(), None, 1006)
        complete_legacy_coverage(ledger)
        missing = MODULE.validate_coverage(ledger)
        self.assertTrue(any("key_items is empty" in item for item in missing))

    def test_structured_equipment_analysis_can_pass(self):
        self.assertEqual(MODULE.validate_coverage(complete_ledger(), stage="draft"), [])

    def test_counts_without_actual_analysis_fail(self):
        ledger = MODULE.build_ledger(synthetic_match(), None, 1006)
        complete_legacy_coverage(ledger)
        complete_equipment_analysis(ledger)
        errors = MODULE.validate_coverage(ledger, stage="draft")
        self.assertTrue(any("global_gameplans" in error for error in errors))

    def test_editable_requirements_cannot_omit_players(self):
        ledger = complete_ledger()
        ledger["equipment_analysis_template"].update(required_player_indices=[], player_item_audits=[])
        ledger["analysis_coverage_template"]["cores"].update(required=0, completed=0)
        ledger["analysis"]["cores"] = []
        errors = MODULE.validate_coverage(ledger, stage="draft")
        self.assertTrue(any("missing player_index" in error for error in errors))
        self.assertTrue(any("requires 6" in error for error in errors))

    def test_missing_source_data_cannot_pass(self):
        ledger = complete_ledger()
        del ledger["source_match"]["version"]
        ledger["source_sha256"] = MODULE.source_digest(ledger["source_match"])
        self.assertTrue(any("source data is not complete" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_source_fact_tampering_is_detected(self):
        ledger = complete_ledger()
        ledger["source_match"]["radiant_win"] = False
        self.assertTrue(any("checksum" in e for e in MODULE.validate_coverage(ledger, "draft")))
        ledger = complete_ledger()
        ledger["players"][0]["hero_id"] = 999
        self.assertTrue(any("source facts" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_draft_does_not_require_claiming_review_complete(self):
        ledger = complete_ledger()
        self.assertEqual(ledger["analysis_coverage_template"]["draft_status"], "NOT_ALLOWED")
        self.assertEqual(MODULE.validate_coverage(ledger, "draft"), [])
        self.assertTrue(any("--review-markdown" in e for e in MODULE.validate_coverage(ledger, "final")))
        self.assertEqual(MODULE.validate_coverage(ledger, "final", review_markdown()), [])

    def test_heading_only_review_fails(self):
        text = "Match 1\n" + "\n".join("## " + title for title in MODULE.SECTION_TITLES)
        self.assertTrue(MODULE.validate_coverage(complete_ledger(), "final", text))

    def test_gameplan_missing_adjustment_fails(self):
        ledger = complete_ledger()
        del ledger["analysis"]["global_gameplans"][0]["adjustment"]
        self.assertTrue(any("adjustment" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_duplicate_fight_and_multiple_core_questions_fail(self):
        ledger = complete_ledger()
        second = copy.deepcopy(ledger["analysis"]["decisive_fights"][0])
        second["id"] = "fight-2"
        second["core_question"] = ["问题甲", "问题乙"]
        ledger["analysis"]["decisive_fights"].append(second)
        errors = MODULE.validate_coverage(ledger, "draft")
        self.assertTrue(any("duplicate time windows" in e for e in errors))
        self.assertTrue(any("exactly one" in e for e in errors))

    def test_unexplained_fight_count_exception_fails(self):
        ledger = complete_ledger()
        ledger["analysis"]["fight_selection"] = {}
        self.assertTrue(any("fight_selection" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_invalid_source_reference_fails(self):
        ledger = complete_ledger()
        ledger["analysis"]["global_gameplans"][0]["evidence"]["refs"] = ["/source_match/no_such_field"]
        self.assertTrue(any("unresolved source ref" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_fabricated_fight_window_fails(self):
        ledger = complete_ledger()
        ledger["analysis"]["decisive_fights"][0].update(start=1000, end=1020)
        self.assertTrue(any("anchor an event" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_wrong_global_plan_link_fails(self):
        ledger = complete_ledger()
        ledger["analysis"]["decisive_fights"][0]["global_gameplan_ids"] = ["missing-plan"]
        self.assertTrue(any("global_gameplan_ids" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_gameplan_cannot_be_marked_as_player_intent_fact(self):
        ledger = complete_ledger()
        ledger["analysis"]["global_gameplans"][0]["evidence"]["judgment"] = "数据明确显示"
        self.assertTrue(any("model judgment" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_bkb_usage_without_purchase_still_requires_audit(self):
        match = synthetic_match()
        match["players"][2]["item_uses"] = {"black_king_bar": 1}
        ledger = MODULE.build_ledger(match, None, 1006)
        self.assertEqual(ledger["equipment_analysis_template"]["required_bkb_player_indices"], [0, 2])

    def test_player_recollection_is_a_separate_source(self):
        ledger = complete_ledger()
        ledger["supplemental_sources"]["user-note"] = {
            "source_type": "player_recollection", "match_id": 1,
            "locator": "用户说明拆卖装备的消息", "time_basis": "approximate_game_seconds",
            "data": {"text": "我记得在该时间段卖掉组件。"},
        }
        item = ledger["equipment_analysis_template"]["player_item_audits"][0]["key_items"][0]
        item["evidence"] = {"source_type": "player_recollection", "judgment": "玩家复述",
                            "refs": ["/supplemental_sources/user-note/data/text"],
                            "limitations": ["玩家回忆没有精确时间戳"]}
        self.assertEqual(MODULE.validate_coverage(ledger, "draft"), [])
        item["evidence"]["judgment"] = "数据明确显示"
        self.assertTrue(any("player recollection" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_unknown_without_reason_fails(self):
        ledger = complete_ledger()
        ledger["analysis"]["global_gameplans"][0]["actual_choices"] = "无法确认"
        self.assertTrue(any("actual_choices" in e for e in MODULE.validate_coverage(ledger, "draft")))
        ledger = complete_ledger()
        ledger["analysis"]["global_gameplans"][0]["evidence"] = evidence(judgment="无法确认")
        self.assertTrue(any("missing-evidence reason" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_model_judgment_label_is_accepted(self):
        ledger = complete_ledger()
        ledger["equipment_analysis_template"]["player_item_audits"][0]["evidence_level"] = "模型判断"
        self.assertEqual(MODULE.validate_coverage(ledger, "draft"), [])

    def test_limited_section_blocks_complete_review(self):
        ledger = complete_ledger()
        ledger["analysis"]["cores"][0]["status"] = "limited"
        self.assertTrue(any("limited or pending" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_empty_event_array_is_distinct_from_missing(self):
        match = synthetic_match()
        match["teamfights"] = []
        self.assertTrue(MODULE.parse_audit(match, {})["strict_complete"])
        del match["teamfights"]
        self.assertFalse(MODULE.parse_audit(match, {})["strict_complete"])

    def test_anonymous_player_is_not_implicitly_the_user(self):
        match = synthetic_match()
        match["players"][0]["account_id"] = None
        self.assertIsNone(MODULE.build_ledger(match, None, None)["equipment_analysis_template"]["user_index"])

    def test_user_deaths_count_comes_from_source(self):
        ledger = complete_ledger()
        ledger["analysis"]["user_deaths"] = [{"id": "fictional-death"}]
        self.assertTrue(any("source death count" in e for e in MODULE.validate_coverage(ledger, "draft")))

    def test_legacy_ledger_requires_regeneration(self):
        self.assertTrue(any("schema_version" in e for e in MODULE.validate_coverage({})))

    def test_cli_draft_and_final_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "ledger.json").write_text(json.dumps(complete_ledger()), encoding="utf-8")
            (path / "review.md").write_text(review_markdown(), encoding="utf-8")
            command = [sys.executable, str(SCRIPT), "--check-ledger", str(path / "ledger.json")]
            draft = subprocess.run(command + ["--stage", "draft"], capture_output=True, text=True)
            self.assertEqual(draft.returncode, 0, draft.stderr + draft.stdout)
            self.assertEqual(json.loads(draft.stdout)["allowed_status"], "DRAFT_ALLOWED")
            final = subprocess.run(command + ["--review-markdown", str(path / "review.md")], capture_output=True, text=True)
            self.assertEqual(final.returncode, 0, final.stderr + final.stdout)
            self.assertEqual(json.loads(final.stdout)["allowed_status"], "REVIEW_COMPLETE")


if __name__ == "__main__":
    unittest.main()
