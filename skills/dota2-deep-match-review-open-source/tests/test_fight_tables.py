"""Per-fight tables must remain paired with their own analysis in final delivery."""
import unittest

from test_extract_match_facts import MODULE as facts, complete_ledger, review_markdown, fight_report


class FightTableTests(unittest.TestCase):
    def test_multiple_fights_match_selected_ids(self):
        report = fight_report('first') + '\n' + fight_report('second')
        self.assertEqual(facts.validate_fight_tables(report, ['first', 'second']), [])
        for ids in (['first'], ['first', 'missing'], ['first', 'second', 'third'], [{}]):
            with self.subTest(ids=ids):
                self.assertTrue(facts.validate_fight_tables(report, ids))
        self.assertTrue(facts.validate_fight_tables(fight_report() * 2))

    def test_prose_only_and_table_only_are_rejected(self):
        report = fight_report()
        variants = [
            '\n'.join(line for line in report.splitlines() if not line.startswith('|')),
            report.split('#### 团战分析')[0],
            report.replace('#### 团战分析', '#### 普通说明'),
            report.replace('#### 本波总结', '#### 普通说明'),
            report.replace('团战分析', '临时标题').replace('本波总结', '团战分析').replace('临时标题', '本波总结'),
        ]
        for text in variants:
            with self.subTest(text=text):
                self.assertTrue(facts.validate_fight_tables(text))

    def test_missing_columns_empty_cells_and_missing_rows_are_rejected(self):
        report = fight_report()
        variants = [report.replace('英雄伤害', '整场伤害'),
                    report.replace('是否参战', '参战依据'),
                    report.replace('| 天辉 |', '|  |'),
                    '\n'.join(line for line in report.splitlines() if not line.startswith('| 天辉 |')),
                    report.replace('| --- |', '| bad |', 1)]
        for text in variants:
            with self.subTest(text=text):
                self.assertTrue(facts.validate_fight_tables(text))

    def test_each_fight_requires_its_own_personal_performance_analysis(self):
        report = fight_report()
        before, rest = report.split('#### 本人本波表现', 1)
        _, summary = rest.split('#### 本波总结', 1)
        without = before + '#### 本波总结' + summary
        empty = before + '#### 本人本波表现\n\n#### 本波总结' + summary
        misplaced = report.replace('本人本波表现', '临时标题').replace('本波总结', '本人本波表现').replace('临时标题', '本波总结')
        for text in (without, empty, misplaced, without + '\n' + fight_report('next')):
            with self.subTest(text=text):
                self.assertTrue(facts.validate_fight_tables(text))
        self.assertEqual(facts.validate_fight_tables(report), [])

    def test_compact_participation_and_optional_healing(self):
        report = fight_report()
        for status in ('是', '否', '无法确认'):
            text = report.replace('| 无法确认 |', '| ' + status + ' |')
            self.assertEqual(facts.validate_fight_tables(text), [])
        self.assertTrue(facts.validate_fight_tables(report.replace('| 无法确认 |', '| 窗口内有行动记录 |')))
        # Optional healing is accepted when the source supplies a real value, including zero.
        for healing in ('0', '624'):
            lines = report.splitlines()
            for i, line in enumerate(lines):
                if line.startswith('| 阵营 |'):
                    lines[i] += ' 治疗记录 |'
                    lines[i + 1] += ' --- |'
                    lines[i + 2] += ' ' + healing + ' |'
            self.assertEqual(facts.validate_fight_tables('\n'.join(lines)), [])
        for column in ('控制记录', '证据与限制'):
            lines = report.splitlines()
            for i, line in enumerate(lines):
                if line.startswith('| 阵营 |'):
                    lines[i] += ' ' + column + ' |'
                    lines[i + 1] += ' --- |'
                    lines[i + 2] += ' 不在表格展示 |'
            self.assertTrue(facts.validate_fight_tables('\n'.join(lines)))

    def test_heading_requires_radiant_then_dire_kill_counts(self):
        report = fight_report()
        label = '天辉 未确认 : 未确认 夜魇'
        self.assertEqual(facts.validate_fight_tables(report.replace(label, '天辉 3 : 5 夜魇')), [])
        self.assertTrue(facts.validate_fight_tables(report.replace(label, '夜魇 5 : 3 天辉')))
        self.assertTrue(facts.validate_fight_tables(report.replace(label, '关键团战')))

    def test_fenced_examples_cannot_satisfy_delivery(self):
        for fence in ('```', '~~~~'):
            for closing in ('', '\n' + fence):
                with self.subTest(fence=fence, closing=closing):
                    self.assertTrue(facts.validate_fight_tables(fence + '\n' + fight_report() + closing))
            self.assertEqual(facts.validate_fight_tables(fence + '\n' + fight_report() + '\n' + fence
                                                        + '\n' + fight_report()), [])

    def test_next_section_or_fight_cannot_supply_missing_content(self):
        report = fight_report()
        for text in (
            report.replace('| 阵营 |', '### 另一个章节\n\n| 阵营 |', 1),
            report.replace('#### 团战分析', '### 下一章节\n\n#### 团战分析', 1),
            report.split('#### 团战分析')[0] + '\n' + fight_report('next'),
        ):
            with self.subTest(text=text):
                self.assertTrue(facts.validate_fight_tables(text))

    def test_zero_fights_requires_explicit_exception_and_no_stray_fight(self):
        self.assertTrue(facts.validate_fight_tables('经检查没有决定性团战。'))
        self.assertEqual(facts.validate_fight_tables('经检查没有决定性团战。', [], allow_empty=True), [])
        self.assertTrue(facts.validate_fight_tables(fight_report(), [], allow_empty=True))

    def test_final_requires_table_but_draft_does_not_require_markdown(self):
        ledger = complete_ledger()
        self.assertEqual(facts.validate_coverage(ledger, 'draft'), [])
        report = '\n'.join(line for line in review_markdown().splitlines() if not line.startswith('|'))
        self.assertTrue(any('table' in error for error in facts.validate_coverage(ledger, 'final', report)))

    def test_original_fight_process_fields_remain_required(self):
        for field in facts.RECORD_FIELDS['decisive_fights']:
            ledger = complete_ledger()
            ledger['analysis']['decisive_fights'][0].pop(field)
            with self.subTest(field=field):
                self.assertTrue(any(field in error for error in facts.validate_coverage(ledger, 'draft')))



if __name__ == '__main__':
    unittest.main()
