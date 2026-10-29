import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import mongomock
import numpy as np
from fastapi import HTTPException, UploadFile
from streamlit.testing.v1 import AppTest
import api.main as api
from db.mongo_store import MongoStore
from scorer.benchmark import analyze_skills
from scorer.fusion import reciprocal_rank_fusion
from scorer.semantic_similarity import EmbeddingCache, chunks
from scorer.text_similarity import calculate_corpus_similarities, TfidfVectorizer
from Parser.resume_parser import parse_resume_bytes


def archive(documents):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as zipped:
        for name, content in documents.items():
            zipped.writestr(name, content)
    return UploadFile(filename="documents.zip", file=io.BytesIO(stream.getvalue()))


class GraphDouble:
    def rank(self, run_id, vacancy, resumes, metrics):
        rows = [{**metric, **analyze_skills(resume["skills"], vacancy["required_skills"])}
                for resume, metric in zip(resumes, metrics)]
        return sorted(rows, key=lambda r: (-r["score"], -r["hybrid_score"], r["resume_id"]))


class ArchiveFlowTests(unittest.TestCase):
    def setUp(self):
        self.db = MongoStore.__new__(MongoStore)
        self.db.client = mongomock.MongoClient()
        self.db.db = self.db.client.verification
        self.db.verify_and_prepare()
        for target, value in (("store", self.db), ("graph", GraphDouble())):
            context = patch.object(api, target, value)
            context.start(); self.addCleanup(context.stop)
        context = patch.object(api, "_write_anonymized_exports")
        context.start(); self.addCleanup(context.stop)
        context = patch.object(api, "calculate_semantic_similarities", side_effect=lambda r, v:
            {"scores": [.8] * len(r), "cache_hits": 0, "cache_misses": len(r)})
        self.semantic = context.start(); self.addCleanup(context.stop)

    def score(self, files=None, uploaded_only=False):
        return api.score_resumes(vacancy_title="Python", vacancy_description="Python Docker",
            required_skills='["Python", "Docker"]', vacancy_id="test", files=files or [], uploaded_only=uploaded_only)

    def test_vacancies_from_archive_are_persisted(self):
        payload = api.upload_vacancies([archive({"python.txt": "Python developer\nRequirements\nPython Docker",
                                               "java.txt": "Java developer\nRequirements\nJava SQL",
                                               "bad.pdf": b"broken"})])
        self.assertEqual(len(payload["vacancies"]), 2)
        self.assertEqual(len(payload["errors"]), 1)
        self.assertEqual(len(self.db.list_vacancies()), 2)

    def test_repeat_upload_skips_parser_and_reads_saved_fields(self):
        content = b"Experience\nBuilt Python services with Docker."
        stored = {"resume_id": "stable", "content_hash": hashlib.sha256(content).hexdigest(),
                  "file_type": "txt", "skills": ["Python"], "anonymized_text": "Python services"}
        self.db.save_resume(stored)
        with patch.object(api, "parse_resume_bytes", side_effect=AssertionError("Must not parse duplicate")):
            result = self.score([archive({"resume.txt": content})], uploaded_only=True)
        self.assertEqual(result["results"][0]["resume_id"], "stable")
        self.assertEqual(result["results"][0]["score"], 50)
        self.assertEqual(self.semantic.call_args.args[0][0]["skills"], ["Python"])
        self.assertEqual(len(self.db.list_resumes()), 1)

    def test_new_resume_saved_and_scored_without_reupload(self):
        first = self.score([archive({"resume.txt": b"Experience\nBuilt Python services with Docker."})])
        second = self.score()
        self.assertEqual(first["results"][0]["resume_id"], second["results"][0]["resume_id"])
        self.assertEqual(len(self.db.list_resumes()), 1)
        self.assertEqual(first["results"][0]["score"], 100)

    def test_empty_professional_resume_is_not_saved(self):
        with self.assertRaises(HTTPException) as error:
            self.score([archive({"empty.txt": b"John Smith\nEmail: john@example.org"})])
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.db.list_resumes(), [])

    def test_bad_archive_does_not_use_old_corpus_when_uploaded_only(self):
        self.db.save_resume({"resume_id": "old", "content_hash": "old", "anonymized_text": "Python"})
        with self.assertRaises(HTTPException) as error:
            self.score([UploadFile(filename="bad.zip", file=io.BytesIO(b"bad"))], uploaded_only=True)
        self.assertEqual(error.exception.status_code, 422)

    def test_failed_graph_does_not_save_successful_history(self):
        with patch.object(api.graph, "rank", side_effect=RuntimeError("offline")):
            with self.assertRaises(HTTPException) as error:
                self.score([archive({"resume.txt": b"Skills\nPython"})])
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.db.list_results("test"), [])
        self.assertEqual(len(self.db.list_resumes()), 1)

    def test_new_catalog_skill_is_found_in_persisted_text_without_reparse(self):
        self.db.save_resume({"resume_id": "saved", "content_hash": "hash", "file_type": "txt",
                             "skills": ["Python"], "anonymized_text": "Built Python and Rust services."})
        with patch.object(api, "parse_resume_bytes", side_effect=AssertionError("No original needed")):
            result = api.score_resumes(vacancy_title="Rust", vacancy_description="Rust",
                required_skills='["Rust"]', vacancy_id="rust", files=[], uploaded_only=False)
        self.assertEqual(result["results"][0]["score"], 100)
        self.assertIn("Rust", self.db.get_resume_by_hash("hash")["skills"])

    def test_initial_ui(self):
        with patch("requests.get") as request:
            request.return_value.json.return_value = []
            app = AppTest.from_file("ui/app.py").run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Подбор кандидатов")
        self.assertEqual(len(app.get("file_uploader")), 2)
        self.assertTrue(app.button[0].disabled)

    def test_ui_works_without_archives_and_displays_tfidf_table(self):
        vacancy = {"id": "test", "title": "Python", "description": "Python", "required_skills": ["Python"]}
        rows = [{"candidate": "Candidate A", "score": 100, "text_similarity": 12,
                 "matched_skills": ["Python"], "missing_skills": []}]
        with patch("requests.get") as get, patch("requests.post") as post:
            get.return_value.json.return_value = [vacancy]
            post.return_value.json.return_value = {"vacancy_title": "Python", "results": rows, "errors": []}
            app = AppTest.from_file("ui/app.py").run()
            app.selectbox[0].select(vacancy).run()
            self.assertFalse(app.button[0].disabled)
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.dataframe[0].value), 1)
            self.assertEqual(len(app.dataframe[0].value.columns), 6)
            self.assertIn("TF-IDF, сходство (0–100)", app.dataframe[0].value.columns)
            self.assertIsNone(post.call_args.kwargs["files"])
            self.assertEqual(post.call_args.kwargs["data"]["uploaded_only"], "false")


class HybridTests(unittest.TestCase):
    def test_e5_json_with_pycharm_terminal_reset(self):
        from types import SimpleNamespace
        from scorer.semantic_similarity import calculate_semantic_similarities
        payload = {"scores": [.8], "cache_hits": 1, "cache_misses": 0}
        for output in (json.dumps(payload), json.dumps(payload) + "\n\x1b[0m",
                       "\x1b[32m" + json.dumps(payload) + "\x1b[0m"):
            with self.subTest(output=output), patch("scorer.semantic_similarity.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=output)):
                result = calculate_semantic_similarities([{"anonymized_text": "Python"}], {"title": "Python"})
                self.assertEqual(result, payload)

    def test_about_me_is_not_exported_as_professional_summary(self):
        text = "Experience\nBuilt Python APIs\nAbout me\nMy name is John Smith, married with two children."
        parsed = parse_resume_bytes("r.txt", text.encode(), ["Python"])
        self.assertIn("Python", parsed.professional_text)
        self.assertNotIn("John Smith", str(parsed.to_anonymized_dict()))

    def test_second_launcher_does_not_stop_existing_instance(self):
        import run_project
        with patch("tools.local_runtime.prepare", side_effect=RuntimeError("Already running")), \
             patch("tools.local_runtime.stop") as stop:
            with self.assertRaises(RuntimeError):
                run_project.main()
        stop.assert_not_called()

    def test_rrf_ties_missing_and_different_scales(self):
        values = reciprocal_rank_fusion([100, 100, 0], [.8, .8, None])
        self.assertEqual(values[0], values[1])
        self.assertGreater(values[0], values[2])
        self.assertAlmostEqual(values[0], 2 / 61)

    def test_tfidf_corpus_has_no_duplicate_current_documents(self):
        vacancy = {"title": "Python", "required_skills": ["Python"]}
        resumes = [{"skills": ["Python"]}, {"skills": ["Java"]}]
        original = TfidfVectorizer.fit_transform
        counts = []
        def observe(self, docs):
            counts.append(len(docs))
            return original(self, docs)
        with patch.object(TfidfVectorizer, "fit_transform", observe):
            calculate_corpus_similarities(resumes, vacancy, resumes, [vacancy])
        self.assertTrue(all(count == 3 for count in counts))

    def test_tfidf_identical_text_documents_keep_idf_when_scoring_subset(self):
        vacancy = {"id": "v", "title": "Python Docker developer", "required_skills": ["Python"]}
        corpus = [{"resume_id": str(i), "skills": ["Python"], "anonymized_text": "Python API services"}
                  for i in range(3)]
        corpus.append({"resume_id": "other", "skills": ["Java"], "anonymized_text": "Java systems"})
        all_values = calculate_corpus_similarities(corpus, vacancy, corpus, [vacancy])
        subset = calculate_corpus_similarities(corpus[:1], vacancy, corpus, [vacancy])
        self.assertEqual(all_values[0], subset[0])

    def test_old_launcher_cleanup_does_not_stop_new_owner(self):
        from pathlib import Path
        import tools.local_runtime as runtime
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            state.write_text(json.dumps([{"role": "api", "owner": "new-owner"}]))
            with patch.object(runtime, "STATE", state), patch.object(runtime.psutil, "Process") as process:
                runtime.stop(owner="old-owner")
            process.assert_not_called()
            self.assertEqual(json.loads(state.read_text())[0]["owner"], "new-owner")

    def test_parser_preserves_duration_and_english_sections(self):
        text = "Professional Experience\nDeveloped Python services for 3 years.\nProjects\nDocker deployment"
        parsed = parse_resume_bytes("r.txt", text.encode(), ["Python", "Docker"])
        self.assertIn("3 years", parsed.professional_text)
        self.assertEqual(parsed.skills, ["Python", "Docker"])

    def test_chunks_keep_end_and_cache_recovers_corruption(self):
        class Tokenizer:
            def encode(self, text, **kwargs):return text.split()
            def decode(self, ids, **kwargs):return " ".join(ids)
        parts = chunks("word " * 1200 + "END", Tokenizer(), "passage: ")
        self.assertTrue(parts[-1].endswith("END"))
        class Model:
            calls = 0
            def encode(self, texts, **kwargs):
                self.calls += 1
                result = np.zeros((len(texts), 768), dtype=np.float32)
                result[:, 0] = 1
                return result
        with tempfile.TemporaryDirectory() as directory:
            model = Model(); cache = EmbeddingCache(model, directory)
            cache.encode(["query: Python"]); cache.encode(["query: Python"])
            self.assertEqual(model.calls, 1)
            from pathlib import Path
            next(Path(directory).glob("*.npy")).write_bytes(b"broken")
            cache.encode(["query: Python"])
            self.assertEqual(model.calls, 2)
