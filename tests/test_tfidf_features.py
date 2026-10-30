"""Vocabulary and mathematical invariants, without databases or E5."""

import math
import unittest

from scorer.expression_catalog import DEFAULT_SKILLS
from scorer.text_similarity import calculate_corpus_similarities, calculate_text_similarity, fit_corpus_tfidf
from scorer.tfidf_features import ProfessionalAnalyzer


class TfidfFeatureTests(unittest.TestCase):
    def analyzer(self, text):
        return ProfessionalAnalyzer(DEFAULT_SKILLS)(text)

    def test_technical_expressions_are_whole_vocabulary_entries(self):
        features = self.analyzer("Node.js .NET CI/CD C++")
        self.assertEqual(features, ["skill:node.js", "skill:.net", "skill:ci/cd", "skill:c++"])

    def test_negative_mentions_do_not_leak_through_residual_words(self):
        features = self.analyzer("Не знаю Docker и Node.js")
        for fragment in ("docker", "node", "js"):
            self.assertFalse(any(fragment in feature for feature in features))
        self.assertIn("word:не", features)

    def test_unknown_professional_words_are_preserved(self):
        self.assertIn("word:elixir", self.analyzer("Разработка Elixir приложений"))

    def test_word_bigrams_do_not_cross_a_masked_expression(self):
        features = self.analyzer("надёжная Python платформа")
        self.assertIn("word:надежная", features)
        self.assertIn("word:платформа", features)
        self.assertNotIn("phrase:надежная платформа", features)

    def test_activity_forms_and_aliases_compare_equally(self):
        resumes = [{"anonymized_text": "Разрабатывал RESTful API"}]
        scores = calculate_corpus_similarities(resumes, {"description": "Разработка REST API"}, resumes, [])
        self.assertEqual(scores, [100.0])

    def test_technical_aliases_compare_equally(self):
        self.assertEqual(calculate_text_similarity("dotnet, CI CD", ".NET, CI/CD"), 100.0)

    def test_node_does_not_match_a_javascript_query(self):
        resumes = [{"anonymized_text": "Node.js"}, {"anonymized_text": "JS"}]
        self.assertEqual(calculate_corpus_similarities(resumes, {"description": "JavaScript"}, resumes, []), [0.0, 100.0])

    def test_python_common_to_all_documents_is_not_pruned(self):
        resumes = [{"anonymized_text": "Python"}, {"anonymized_text": "Не знаю Python"}]
        self.assertEqual(calculate_corpus_similarities(resumes, {"description": "Python"}, resumes, []), [100.0, 0.0])

    def test_custom_skill_catalog_is_shared_between_query_and_documents(self):
        resumes = [{"anonymized_text": "Elixir"}, {"anonymized_text": "Без опыта Elixir"}]
        vacancy = {"required_skills": ["Elixir"]}
        self.assertEqual(calculate_corpus_similarities(resumes, vacancy, resumes, []), [100.0, 0.0])

    def test_required_skill_has_more_frequency_boost_than_a_preference(self):
        resumes = [{"anonymized_text": "Python"}, {"anonymized_text": "Docker"}]
        vacancy = {"required_skills": ["Python"], "preferred_skills": ["Docker"]}
        values = calculate_corpus_similarities(resumes, vacancy, resumes, [])
        self.assertGreater(values[0], values[1])
        self.assertGreater(values[1], 0)

    def test_relevant_professional_resume_ranks_above_an_unrelated_one(self):
        vacancy = {"title": "Python backend", "required_skills": ["Python", "FastAPI"], "description": "build REST API"}
        resumes = [
            {"title": "Backend engineer", "skills": ["Python", "FastAPI"], "experience": "build REST API services"},
            {"title": "Accountant", "skills": ["Excel"], "experience": "financial reporting"},
        ]
        values = calculate_corpus_similarities(resumes, vacancy, resumes, [vacancy])
        self.assertGreater(values[0], values[1])

    def test_negation_does_not_cross_document_fields(self):
        resume = {"experience": "Без опыта Docker", "anonymized_text": "Python"}
        score = calculate_corpus_similarities([resume], {"description": "Python"}, [resume], [])[0]
        self.assertGreater(score, 0)

    def test_smoothed_idf_and_l2_normalization(self):
        resumes = [{"anonymized_text": "Python"}, {"anonymized_text": "Python Docker"}]
        fitted = fit_corpus_tfidf(resumes, {"description": "Python"}, resumes, [])
        vocabulary = fitted.vectorizer.vocabulary_
        self.assertEqual(len(fitted.documents), 3)
        self.assertAlmostEqual(fitted.vectorizer.idf_[vocabulary["skill:python"]], 1.0)
        self.assertAlmostEqual(fitted.vectorizer.idf_[vocabulary["skill:docker"]], 1 + math.log(4 / 2))
        for row in range(3):
            self.assertAlmostEqual(float(fitted.matrix[row].multiply(fitted.matrix[row]).sum()), 1.0)

    def test_frequency_boost_is_logarithmic_not_a_linear_field_weight(self):
        resume = {"title": "Python", "anonymized_text": "Docker"}
        fitted = fit_corpus_tfidf([resume], {"description": "Python Docker"}, [resume], [])
        row = fitted.matrix[fitted.candidate_indices[0]]
        vocabulary = fitted.vectorizer.vocabulary_
        ratio = row[0, vocabulary["skill:python"]] / row[0, vocabulary["skill:docker"]]
        self.assertAlmostEqual(ratio, 1 + math.log(3))

    def test_repeated_current_inputs_do_not_inflate_the_fit_corpus(self):
        vacancy = {"id": "v", "description": "Python"}
        resume = {"resume_id": "r", "anonymized_text": "Python"}
        fitted = fit_corpus_tfidf([resume, resume], vacancy, [resume, resume], [vacancy, vacancy])
        self.assertEqual(len(fitted.documents), 2)
        self.assertEqual(fitted.candidate_indices[0], fitted.candidate_indices[1])
        self.assertEqual(calculate_corpus_similarities([resume, resume], vacancy, [resume], [vacancy]), [100.0, 100.0])

    def test_identical_texts_with_different_ids_remain_distinct_documents(self):
        resumes = [{"resume_id": str(index), "anonymized_text": "Python"} for index in range(3)]
        fitted = fit_corpus_tfidf(resumes, {"id": "v", "description": "Python"}, resumes, [])
        self.assertEqual(len(fitted.documents), 4)

    def test_subset_scores_use_the_same_persisted_corpus(self):
        vacancy = {"id": "v", "required_skills": ["Python"]}
        corpus = [{"resume_id": str(index), "anonymized_text": "Python API services"} for index in range(3)]
        corpus.append({"resume_id": "other", "anonymized_text": "Java systems"})
        all_values = calculate_corpus_similarities(corpus, vacancy, corpus, [vacancy])
        subset = calculate_corpus_similarities(corpus[:1], vacancy, corpus, [vacancy])
        self.assertEqual(all_values[0], subset[0])

    def test_conflicting_comparison_texts_for_one_id_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "document ID"):
            fit_corpus_tfidf([
                {"resume_id": "r", "anonymized_text": "Python"},
                {"resume_id": "r", "anonymized_text": "Java"},
            ], {"description": "Python"}, [], [])

    def test_empty_and_zero_vectors_have_defined_results(self):
        self.assertEqual(calculate_corpus_similarities([], {}, [], []), [])
        self.assertEqual(calculate_corpus_similarities([{}], {}, [], []), [None])
        self.assertEqual(calculate_corpus_similarities([{}], {"description": "Python"}, [], []), [0.0])
        self.assertEqual(calculate_text_similarity("и в на", "и в на"), 0.0)


if __name__ == "__main__":
    unittest.main()
