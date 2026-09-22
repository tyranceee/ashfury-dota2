"""Offline structural tests; fixtures are not reviews of real matches."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_extract_match_facts import synthetic_match, evidence, fight_report

SCRIPT = Path(__file__).parents[1] / "scripts" / "cooperative_review.py"
sys.path.insert(0, str(SCRIPT.parent))
import cooperative_review as coop


def markdown(keys, prefix="底稿"):
    return "\n\n".join(
        f"<!-- review-section: {key} -->\n## {key}\n\n{prefix}：结构测试内容，不是真实比赛判断。"
        + ("\n\n" + fight_report() if key == "fights" else "")
        for key in keys
    ) + "\n"


class CooperativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.match = self.root / "match.json"
        self.base = self.root / "base.md"
        self.match.write_text(json.dumps(synthetic_match()), encoding="utf-8")
        self.base.write_text("<!-- match_id=1 -->\n" + markdown(coop.SECTIONS + ("handoff",)),
                             encoding="utf-8")
        self.audit = coop.prepare(self.match, self.base, 1000)
        self.replacements = markdown([k for k in coop.SECTIONS if k in coop.REWRITE], "重写")

    def completed(self):
        audit = copy.deepcopy(self.audit)
        audit["intake_note"] = "同场身份已核对；上游数据版本无指纹可比，未核验。"
        for record in audit["checks"].values():
            record.update(note="合成结构样例仅检查索引来源，不声称战术正确。", evidence=evidence())
        for key, record in audit["sections"].items():
            record.update(action="rewrite" if key in coop.REWRITE else "adopt",
                          review="rewritten" if key in coop.REWRITE else "sampled",
                          note="合成样例的来源引用可解析；不证明章节事实真伪。",
                          evidence=evidence())
        audit["training_items"] = [{"signal": "关键目标前保护资源不足",
            "action": "确认接应条件", "tradeoff": "可能让出先手机会", "evidence": evidence()}]
        audit["claim_audits"] = [
            {"id": "C1", "section": "points", "claim": "玩家零号英雄 ID 是 1。",
             "verdict": "supported", "handling": "retained",
             "reason": "合成原字段值相符。", "evidence": evidence(judgment="数据明确显示")}
        ]
        return audit

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True)

    def artifacts(self, audit=None):
        audit_path, replacements = self.root / "audit.json", self.root / "replacements.md"
        audit_path.write_text(json.dumps(audit or self.completed()), encoding="utf-8")
        replacements.write_text(self.replacements, encoding="utf-8")
        return audit_path, replacements

    def test_compact_index_has_raw_pointers_without_raw_duplicate(self):
        self.assertNotIn("source_match", self.audit)
        self.assertEqual(self.audit["index"]["players"][0]["event_index"]["purchase_log"][0]["ref"],
                         "/source_match/players/0/purchase_log/0")
        self.assertEqual(len(self.audit["index"]["players"]), 10)
        self.assertEqual(self.audit["intake_note"], "PENDING")

    def test_unfinished_audit_cannot_pass(self):
        errors, _, _ = coop.validate(self.audit, self.replacements)
        self.assertTrue(errors)
        self.assertTrue(any("intake_note" in e for e in errors))

    def test_assembly_preserves_adopted_and_replaces_mandatory_sections(self):
        audit = self.completed()
        self.assertEqual(coop.validate(audit, self.replacements)[0], [])
        final = coop.assemble(audit, self.replacements)
        self.assertIn(coop.nest_section(coop.sections(self.base.read_text())["lanes"]), final)
        self.assertIn(coop.nest_section(coop.sections(self.replacements)["equipment"]), final)
        self.assertNotIn("handoff", final)
        self.assertNotIn("review-section:", final)
        self.assertIn("抽查", final)

    def test_mandatory_sections_cannot_be_adopted(self):
        for key in coop.REWRITE:
            with self.subTest(section=key):
                audit = self.completed()
                audit["sections"][key].update(action="adopt", review="sampled")
                self.assertTrue(coop.validate(audit, self.replacements)[0])

    def test_final_discloses_sampled_verified_and_rewritten_separately(self):
        audit = self.completed()
        audit["sections"]["points"]["review"] = "verified"
        final = coop.assemble(audit, self.replacements)
        self.assertIn("| 本场要点 | GPT 核验采用 |", final)
        self.assertIn("| 三路对线 | GPT 抽查采用 |", final)
        self.assertIn("| 装备 | GPT 重写 |", final)

    def test_personal_improvement_ends_report_without_audit_appendix(self):
        final = coop.assemble(self.completed(), self.replacements)
        self.assertLess(final.index("### 审核范围与限制"), final.index("## 阵容与三路对线"))
        self.assertTrue(final.rstrip().endswith(coop.nest_section(coop.sections(self.replacements)["training"])))
        self.assertIn("## 本人对局改进意见", final)
        self.assertNotIn("训练建议与验收", final)

    def test_curve_indices_are_not_assumed_to_be_minutes(self):
        match = synthetic_match()
        match["players"][0].update(gold_t=[10, 20], times=[-60, 0])
        row = coop.compact_index(match, 1000)["players"][0]
        self.assertEqual(row["curve_samples"]["gold_t"]["0"], 10)
        self.assertEqual(row["sample_times"]["0"], -60)

    def test_null_teamfights_preserves_partial_source_status(self):
        match = synthetic_match()
        match["teamfights"] = None
        index = coop.compact_index(match, 1000)
        self.assertEqual(index["fight_index"], [])
        self.assertFalse(index["parse_audit"]["strict_complete"])

    def test_missing_duplicate_and_unknown_sections_fail(self):
        for replacement in (
            markdown(["summary"]),
            self.replacements + markdown(["equipment"]),
            self.replacements + markdown(["unexpected"]),
        ):
            with self.subTest(replacement=replacement[:60]):
                self.assertTrue(coop.validate(self.completed(), replacement)[0])

    def test_heading_only_and_fenced_markers_do_not_count(self):
        with self.assertRaises(ValueError):
            coop.sections("<!-- review-section: summary -->\n## Summary\n")
        fenced = "```md\n<!-- review-section: equipment -->\n## example\nnot real\n```\n"
        self.assertNotIn("equipment", coop.sections(fenced + markdown(["summary"])))

    def test_wrong_match_in_initial_is_rejected(self):
        self.base.write_text("<!-- match_id=2 -->\n" + markdown(coop.SECTIONS))
        with self.assertRaisesRegex(ValueError, "different match"):
            coop.prepare(self.match, self.base, 1000)

    def test_changed_source_and_changed_initial_are_rejected(self):
        audit = self.completed()
        original = self.match.read_text()
        match = json.loads(original)
        match["duration"] = 1801
        self.match.write_text(json.dumps(match))
        self.assertIn("source changed", coop.validate(audit, self.replacements)[0][0])
        self.match.write_text(original)
        self.base.write_text(self.base.read_text() + "\nchanged")
        self.assertIn("preliminary changed", coop.validate(audit, self.replacements)[0][0])

    def test_tampered_index_is_rejected(self):
        audit = self.completed()
        audit["index"]["objectives"] = []
        self.assertIn("deterministic index differs from source",
                      coop.validate(audit, self.replacements)[0])

    def test_partial_source_or_unknown_user_cannot_pass(self):
        match = synthetic_match()
        del match["teamfights"]
        self.match.write_text(json.dumps(match))
        audit = self.completed()
        fresh = coop.prepare(self.match, self.base, 999999)
        for key in ("source_sha256", "index", "user_account_id"):
            audit[key] = fresh[key]
        errors = coop.validate(audit, self.replacements)[0]
        self.assertIn("user not identified", errors)
        self.assertIn("partial source cannot pass cooperative-complete gate", errors)

    def test_disputed_claim_requires_affected_section_rewrite(self):
        audit = self.completed()
        audit["claim_audits"][0].update(verdict="refuted", handling="removed")
        self.assertTrue(any("disputed claim" in e for e in coop.validate(audit, self.replacements)[0]))
        audit["sections"]["points"].update(action="rewrite", review="rewritten")
        self.assertEqual(coop.validate(audit, self.replacements + markdown(["points"], "修正"))[0], [])

    def test_unsupported_claim_cannot_be_retained(self):
        audit = self.completed()
        audit["claim_audits"][0].update(verdict="unsupported", handling="retained")
        self.assertTrue(any("verdict/handling" in e for e in coop.validate(audit, self.replacements)[0]))

    def test_model_analysis_not_valid_as_evidence(self):
        audit = self.completed()
        audit["claim_audits"][0]["evidence"]["refs"] = ["/preliminary_review/claim"]
        self.assertTrue(any("unresolved source ref" in e for e in coop.validate(audit, self.replacements)[0]))

    def test_cli_final_requires_exact_assembly_and_semantic_attestation(self):
        audit_path, replacements = self.artifacts()
        final = self.root / "final.md"
        result = self.run_cli("merge", audit_path, "--replacements", replacements, "--output", final)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_cli("check", audit_path, "--replacements", replacements, "--final", final)
        self.assertEqual(result.returncode, 3)
        self.assertIn("semantic", result.stdout)
        audit = self.completed()
        audit["final_semantic_check"] = {"status": "complete", "note": "合成测试已核对章节选择与来源，不宣称真实战术正确。"}
        audit_path.write_text(json.dumps(audit))
        result = self.run_cli("check", audit_path, "--replacements", replacements, "--final", final)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("COOPERATIVE_REVIEW_COMPLETE", result.stdout)
        final.write_text(final.read_text() + "\nunaudited")
        self.assertEqual(self.run_cli("check", audit_path, "--replacements", replacements,
                                     "--final", final).returncode, 3)

    def test_cli_does_not_overwrite_existing_output(self):
        existing = self.root / "keep.md"
        existing.write_text("keep")
        result = self.run_cli("prepare", self.match, "--preliminary", self.base,
                             "--user-account-id", "1000", "--output", existing)
        self.assertEqual(result.returncode, 3)
        audit, replacement = self.artifacts()
        result = self.run_cli("merge", audit, "--replacements", replacement, "--output", existing)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(existing.read_text(), "keep")

    def test_cli_get_returns_real_values_or_rejects_oversize(self):
        audit, _ = self.artifacts()
        result = self.run_cli("get", audit, "--pointer", "/source_match/players/0/hero_id")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["/source_match/players/0/hero_id"], 1)
        result = self.run_cli("get", audit, "--pointer", "/source_match/players", "--max-chars", "5")
        self.assertEqual(result.returncode, 3)
        self.assertIn("exceeds bound", result.stdout)

    def test_standalone_validator_rejects_cooperative_schema(self):
        audit, _ = self.artifacts()
        standalone = SCRIPT.with_name("extract_match_facts.py")
        result = subprocess.run([sys.executable, str(standalone), "--check-ledger", str(audit)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("schema", result.stdout.lower())


if __name__ == "__main__":
    unittest.main()
