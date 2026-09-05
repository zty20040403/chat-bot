from __future__ import annotations

import unittest
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from src.context_evaluation import evaluation_report, score_answer


class ContextEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.case = {
            "name": "follow-up", "category": "follow_up",
            "messages": [
                {"id": 1, "user": 7, "sender": "Alice", "text": "h610 延迟变高了"},
                {"id": 2, "user": 8, "sender": "Bob", "text": "晚饭吃什么"},
            ],
            "current": {"id": 3, "user": 9, "text": "你觉得呢"},
            "expected_focus": 1, "required": ["h610"], "forbidden": ["晚饭"],
        }
        self.good = {
            "case_id": "follow-up:0", "answer": "先检查 h610 的数据库响应时间。",
            "focus_id": 1, "evidence_ids": [1], "personal_facts": [],
        }

    def test_correct_answer_and_evidence_pass(self):
        report = evaluation_report([self.case], [self.good])
        self.assertTrue(report["passed"])
        self.assertEqual(report["metrics"]["focus_accuracy"], 1)

    def test_having_all_original_messages_does_not_make_wrong_answer_pass(self):
        wrong = {**self.good, "answer": "晚饭吃面", "focus_id": 2, "evidence_ids": [1, 2]}
        report = evaluation_report([self.case], [wrong])
        self.assertFalse(report["passed"])
        self.assertEqual(report["metrics"]["focus_accuracy"], 0)
        self.assertIn("unrelated_answer_content", report["cases"][0]["reasons"])

    def test_wrong_scope_user_and_fabricated_evidence_fail_independently(self):
        for mutation, reason in (
            ({"scope_key": "onebot-v11:group:200"}, "cross_scope"),
            ({"personal_facts": [{"user_id": 9, "evidence_id": 1}]}, "wrong_personal_fact_owner"),
            ({"evidence_ids": [999]}, "missing_or_unknown_evidence"),
        ):
            with self.subTest(reason=reason):
                score = score_answer("follow-up:0", self.case, {**self.good, **mutation})
                self.assertFalse(score.answer_pass)
                self.assertIn(reason, score.reasons)

    def test_missing_and_error_predictions_are_not_removed_from_denominator(self):
        for predictions in ([], [{"case_id": "follow-up:0", "error": "timeout"}]):
            report = evaluation_report([self.case], predictions)
            self.assertFalse(report["passed"])
            self.assertEqual(report["metrics"]["answered"], 0)
            self.assertEqual(report["metrics"]["prompt_count"], 1)
            self.assertEqual(report["metrics"]["focus_accuracy"], 0)

    def test_actual_retrieval_order_is_scored_not_fixture_order(self):
        case = {**self.case, "evaluation": "recall", "expected_recall": 1, "semantic_order": [1]}
        score = score_answer("follow-up:0", case, {**self.good, "retrieved_ids": [2, 3, 4, 5, 6, 1]})
        self.assertFalse(score.recall_at_five)
        self.assertFalse(score.answer_pass)

    def test_duplicate_or_unknown_results_are_rejected(self):
        for predictions in ([self.good, self.good], [{"case_id": "made-up"}]):
            with self.assertRaises(ValueError):
                evaluation_report([self.case], predictions)

    def test_offline_cli_scores_without_loading_the_bot(self):
        tool = Path(__file__).resolve().parents[1] / "tools/context_eval.py"
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "cases.json").write_text(json.dumps([self.case]))
            (root / "answers.json").write_text(json.dumps([self.good]))
            result = subprocess.run([
                sys.executable, str(tool), "--cases", str(root / "cases.json"),
                "--predictions", str(root / "answers.json"), "--output", str(root / "report.json"),
            ], capture_output=True, text=True, timeout=5, cwd=temporary)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("NoneBot", result.stdout)
            report = json.loads((root / "report.json").read_text())
            self.assertTrue(report["passed"])
            self.assertEqual(len(report["fixture_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
