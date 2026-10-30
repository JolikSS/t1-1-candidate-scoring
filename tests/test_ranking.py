import math
import unittest

from scorer.ranking import RANKING_VERSION, build_candidate_metrics, public_result, rank_result_rows


class RankingTests(unittest.TestCase):
    def row(self, identifier, coverage, lexical):
        return {"resume_id": identifier, "score": coverage, "text_similarity": lexical}

    def test_coverage_has_priority_over_tfidf(self):
        rows = [self.row("less", 50, 100), self.row("complete", 100, 1)]
        self.assertEqual([row["resume_id"] for row in rank_result_rows(rows)], ["complete", "less"])

    def test_tfidf_breaks_equal_coverage(self):
        rows = [self.row("lower", 100, 10), self.row("higher", 100, 80)]
        self.assertEqual([row["resume_id"] for row in rank_result_rows(rows)], ["higher", "lower"])

    def test_stable_id_breaks_complete_ties(self):
        rows = [self.row("b", 100, 10), self.row("a", 100, 10)]
        self.assertEqual([row["resume_id"] for row in rank_result_rows(rows)], ["a", "b"])
        self.assertEqual(rank_result_rows(rows), rank_result_rows(list(reversed(rows))))

    def test_missing_similarity_is_after_zero_at_equal_coverage(self):
        rows = [self.row("missing", 0, None), self.row("zero", 0, 0)]
        self.assertEqual([row["resume_id"] for row in rank_result_rows(rows)], ["zero", "missing"])

    def test_invalid_scores_are_rejected(self):
        for value in (math.nan, math.inf, -1, 101, True, "80"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rank_result_rows([self.row("r", 100, value)])

    def test_metric_alignment_and_anonymous_labels(self):
        rows = build_candidate_metrics([{"resume_id": "abc123456", "file_type": "txt"}], [42])
        self.assertEqual(rows, [{"resume_id": "abc123456", "candidate": "Кандидат 123456",
                                "file_type": "txt", "file_number": 1, "text_similarity": 42.0}])

    def test_incomplete_metric_batch_is_rejected(self):
        with self.assertRaises(ValueError):
            build_candidate_metrics([{"resume_id": "r"}], [])

    def test_duplicate_ids_are_rejected_before_graph_materialization(self):
        with self.assertRaises(ValueError):
            build_candidate_metrics([{"resume_id": "r"}, {"resume_id": "r"}], [1, 2])

    def test_missing_id_is_rejected(self):
        with self.assertRaises(ValueError):
            build_candidate_metrics([{}], [1])

    def test_history_projection_discards_unknown_metrics(self):
        row = {**self.row("r", 100, 10), "retired_metric": 0.9}
        projected = public_result(row)
        self.assertNotIn("retired_metric", projected)
        self.assertEqual(projected["ranking_version"], "legacy")
        self.assertIn("retired_metric", row)

    def test_history_projection_keeps_an_explicit_version(self):
        self.assertEqual(public_result({"ranking_version": RANKING_VERSION})["ranking_version"], RANKING_VERSION)


if __name__ == "__main__":
    unittest.main()
