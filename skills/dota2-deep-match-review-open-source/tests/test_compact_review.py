"""Compact output must reduce repetition without bypassing identity or evidence gates."""
import unittest

from test_extract_match_facts import MODULE as facts, complete_ledger, review_markdown


def use_brief_records(ledger):
    for category, fields in {
        "cores": ("role_basis", "primary_secondary_roles", "conclusion", "comparison", "bkb_necessity", "bkb_tradeoff"),
        "supports": ("role_basis", "conclusion"),
    }.items():
        ledger["analysis"][category] = [
            {**{key: row[key] for key in ("id", "status", "player_index", "evidence", *fields)
                if key in row}, "detail": "brief",
             "screening": "已查资源、参战、死亡及目标；合成样例用于验证简述结构。"}
            for row in ledger["analysis"][category]
        ]
    for row in ledger["equipment_analysis_template"]["player_item_audits"]:
        row.update(detail="brief", screening="已查路线与窗口；合成样例无额外重大争议。", key_items=[])
        row.pop("best_item_decision", None)
        row.pop("biggest_item_issue", None)


class CompactStandaloneTests(unittest.TestCase):
    def test_gameplan_first_layout_passes_with_full_underlying_coverage(self):
        ledger = complete_ledger()
        self.assertEqual(len(facts.SECTION_TITLES), 8)
        self.assertEqual(facts.SECTION_TITLES[0], "全局博弈")
        self.assertEqual(facts.validate_coverage(ledger, "final", review_markdown()), [])

    def test_legacy_report_layout_still_passes_structure_check(self):
        report = "比赛 1\n\n" + "\n\n".join("## " + title + "\n\n合成测试正文。"
                                                for title in facts.LEGACY_SECTION_TITLES)
        self.assertEqual(facts.validate_markdown(report, 1), [])

    def test_strategy_is_required_as_peer_chapter_before_fights(self):
        sections = ["## " + title + "\n\n合成测试正文。" for title in facts.SECTION_TITLES]
        strategy = next(s for s in sections if "团队策略分析" in s)
        missing = [s for s in sections if s != strategy]
        nested = [s.replace("## 团队策略分析", "### 团队策略分析") for s in sections]
        after_fights = missing[:4] + [strategy] + missing[4:]
        for variant in (missing, nested, after_fights):
            with self.subTest(variant=variant):
                self.assertTrue(facts.validate_markdown("比赛 1\n\n" + "\n\n".join(variant), 1))

    def test_old_fragment_heading_does_not_switch_new_report_to_legacy_mode(self):
        report = review_markdown().replace("合成测试正文。", "### 比赛结论与用户概览\n\n合成测试正文。", 1)
        self.assertEqual(facts.validate_markdown(report, 1), [])

    def test_missing_duplicate_reordered_and_empty_topics_fail(self):
        sections = ["## " + title + "\n\n合成测试正文。" for title in facts.SECTION_TITLES]
        variants = [sections[:-1], sections + sections[:1], list(reversed(sections)),
                    ["## " + facts.SECTION_TITLES[0]] + sections[1:]]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertTrue(facts.validate_markdown("比赛 1\n\n" + "\n\n".join(variant), 1))

    def test_different_topic_levels_fail(self):
        report = review_markdown().replace("## " + facts.SECTION_TITLES[2],
                                            "### " + facts.SECTION_TITLES[2])
        self.assertTrue(facts.validate_markdown(report, 1))

    def test_full_review_requires_every_player(self):
        for category in ("cores", "supports", "support_lane_pressure"):
            ledger = complete_ledger()
            ledger["analysis"][category].pop()
            with self.subTest(category=category):
                self.assertTrue(facts.validate_coverage(ledger, "draft"))

    def test_former_brief_records_cannot_pass_full_review(self):
        ledger = complete_ledger()
        use_brief_records(ledger)
        errors = facts.validate_coverage(ledger, "draft")
        self.assertTrue(any("requires detailed player analysis" in error for error in errors))
        self.assertTrue(any("requires detailed equipment analysis" in error for error in errors))
        # Relabeling alone must not restore a pass: actual process fields remain absent.
        for category in ("cores", "supports"):
            for row in ledger["analysis"][category]:
                row["detail"] = "detailed"
        for row in ledger["equipment_analysis_template"]["player_item_audits"]:
            row["detail"] = "detailed"
        self.assertTrue(facts.validate_coverage(ledger, "draft"))

    def test_core_process_and_comparison_fields_cannot_be_omitted(self):
        for field in ("economy_curve", "lane_and_recovery", "item_windows", "participation_and_targets",
                      "team_enabling", "deaths_buybacks", "map_conversion", "comparison",
                      "bkb_necessity", "bkb_tradeoff"):
            ledger = complete_ledger()
            ledger["analysis"]["cores"][0].pop(field)
            with self.subTest(field=field):
                errors = facts.validate_coverage(ledger, "draft")
                self.assertTrue(any(field in error for error in errors))

    def test_support_process_fields_cannot_be_omitted(self):
        for field in ("lane_conversion", "vision", "control_and_saves", "key_deaths", "equipment_fit"):
            ledger = complete_ledger()
            ledger["analysis"]["supports"][0].pop(field)
            with self.subTest(field=field):
                self.assertTrue(any(field in error for error in facts.validate_coverage(ledger, "draft")))

    def test_empty_equipment_and_missing_alternative_cost_cannot_pass(self):
        ledger = complete_ledger()
        ledger["equipment_analysis_template"]["player_item_audits"][0]["key_items"] = []
        self.assertTrue(any("key_items is empty" in error for error in facts.validate_coverage(ledger, "draft")))
        ledger = complete_ledger()
        item = ledger["equipment_analysis_template"]["player_item_audits"][0]["key_items"][0]
        item.pop("alternative_and_cost")
        self.assertTrue(any("alternative_and_cost" in error for error in facts.validate_coverage(ledger, "draft")))

    def test_equipment_cannot_skip_bkb_or_aegis_audits(self):
        for key in ("bkb_player_audits", "aegis_lifecycle_audits"):
            ledger = complete_ledger()
            ledger["equipment_analysis_template"][key] = []
            with self.subTest(key=key):
                self.assertTrue(facts.validate_coverage(ledger, "draft"))

    def test_single_relevant_window_does_not_require_negative_example(self):
        ledger = complete_ledger()
        for row in ledger["analysis"]["key_skills"]:
            self.assertNotIn("low_or_unrecorded_window", row)
        holder = ledger["equipment_analysis_template"]["bkb_player_audits"][0]
        self.assertNotIn("low_value_or_uncovered_window", holder)
        self.assertEqual(facts.validate_coverage(ledger, "draft"), [])

    def test_legacy_paired_windows_are_accepted_but_new_blank_does_not_fall_back(self):
        ledger = complete_ledger()
        skill = ledger["analysis"]["key_skills"][0]
        skill["high_value_window"] = skill.pop("window_analysis")
        skill["low_or_unrecorded_window"] = "不适用：测试样例无其他窗口"
        holder = ledger["equipment_analysis_template"]["bkb_player_audits"][0]
        holder["high_value_window"] = holder.pop("window_analysis")
        holder["low_value_or_uncovered_window"] = "不适用：测试样例无其他窗口"
        self.assertEqual(facts.validate_coverage(ledger, "draft"), [])
        skill["window_analysis"] = "PENDING"
        self.assertTrue(facts.validate_coverage(ledger, "draft"))

    def test_global_gameplans_require_two_or_three_distinct_records(self):
        for count in (0, 1, 2, 3, 4):
            ledger = complete_ledger()
            plans = ledger["analysis"]["global_gameplans"]
            ledger["analysis"]["global_gameplans"] = [
                {**plans[i % len(plans)], "id": f"plan-{i + 1}"} for i in range(count)]
            ledger["analysis"]["decisive_fights"][0]["global_gameplan_ids"] = [
                row["id"] for row in ledger["analysis"]["global_gameplans"]]
            errors = facts.validate_coverage(ledger, "draft")
            with self.subTest(count=count):
                if count in (2, 3):
                    self.assertEqual(errors, [])
                else:
                    self.assertTrue(any("global_gameplans requires 2-3" in error for error in errors))

    def test_training_requires_one_to_three_evidenced_actions(self):
        for count in (0, 4):
            ledger = complete_ledger()
            item = ledger["analysis"]["training"][0]
            ledger["analysis"]["training"] = [{**item, "id": f"training-{i}"} for i in range(count)]
            with self.subTest(count=count):
                self.assertTrue(facts.validate_coverage(ledger, "draft"))
        for field in ("signal", "action", "tradeoff", "evidence"):
            ledger = complete_ledger()
            ledger["analysis"]["training"][0].pop(field)
            with self.subTest(field=field):
                self.assertTrue(facts.validate_coverage(ledger, "draft"))



if __name__ == "__main__":
    unittest.main()
