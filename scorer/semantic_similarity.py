"""Isolated E5 worker: the API never imports the large model runtime."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Lock

E5_REVISION = "d128750597153bb5987e10b1c3493a34e5a4502a"
PREPARATION_VERSION = "sections-448-overlap64-v1"
WORKER_LOCK = Lock()


def chunks(text, tokenizer, prefix, limit=448, overlap=64):
    import re
    if prefix not in {"query: ", "passage: "}:
        raise ValueError("Invalid E5 prefix")
    result = []
    for paragraph in re.split(r"\n\s*\n", text):
        ids = tokenizer.encode(paragraph.strip(), add_special_tokens=False, truncation=False, verbose=False)
        for start in range(0, len(ids), limit - overlap):
            value = prefix + tokenizer.decode(ids[start:start + limit], skip_special_tokens=True)
            if len(tokenizer.encode(value, add_special_tokens=True)) > 512:
                raise ValueError("Encoded fragment exceeds model context")
            result.append(value)
            if start + limit >= len(ids):
                break
    return result


class EmbeddingCache:
    def __init__(self, model, directory):
        self.model, self.directory = model, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.hits = self.misses = 0

    def encode(self, texts):
        import numpy as np
        values, missing = [], []
        for text in texts:
            key = hashlib.sha256((E5_REVISION + PREPARATION_VERSION + text).encode()).hexdigest()
            path = self.directory / (key + ".npy")
            value = None
            try:
                value = np.load(path, allow_pickle=False)
                if value.shape != (768,) or not np.isfinite(value).all() or abs(np.linalg.norm(value) - 1) > .01:
                    value = None
            except (OSError, ValueError, EOFError):
                pass
            if value is None:
                missing.append((len(values), text, path))
                self.misses += 1
            else:
                self.hits += 1
            values.append(value)
        if missing:
            vectors = self.model.encode([item[1] for item in missing], normalize_embeddings=True,
                                        batch_size=8, show_progress_bar=False)
            for (index, _, path), vector in zip(missing, vectors):
                values[index] = vector
                # Unique temporary names allow simultaneous independent API runs.
                with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as temp:
                    np.save(temp, vector, allow_pickle=False)
                    temporary = Path(temp.name)
                temporary.replace(path)
        return np.asarray(values, dtype=np.float32).reshape((-1, 768))


def calculate_semantic_similarities(resumes, vacancy):
    environment = os.environ.copy()
    environment.update(PYTHONIOENCODING="utf-8", TOKENIZERS_PARALLELISM="false")
    if environment.get("EMBEDDING_EXTRA_PATH"):
        environment["PYTHONPATH"] = environment["EMBEDDING_EXTRA_PATH"]
    payload = {"vacancy": {key: vacancy.get(key, [] if key == "required_skills" else "")
                           for key in ("title", "required_skills", "description")},
               "resumes": [{"anonymized_text": resume.get("anonymized_text", "")} for resume in resumes]}
    with WORKER_LOCK:
        process = subprocess.run([os.getenv("EMBEDDING_PYTHON", sys.executable), str(Path(__file__).resolve())],
                                 input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True,
                                 env=environment, timeout=1800)
    if process.returncode:
        # Never propagate model logs or document fragments into HTTP errors.
        raise RuntimeError("E5 worker failed; check model path and embedding environment")
    # PyCharm/colorama can append a terminal reset after the worker's JSON.
    output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", process.stdout)
    result = json.loads(output)
    if len(result["scores"]) != len(resumes):
        raise RuntimeError("E5 returned an incomplete batch")
    return result


def worker():
    import psutil
    available = psutil.virtual_memory().available
    if os.name == "nt":
        import ctypes
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        *[(name, ctypes.c_ulonglong) for name in
                          ("total_phys", "avail_phys", "total_page", "avail_page", "total_virtual", "avail_virtual", "extended")]]
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            available = status.avail_page
    # Windows can page model weights; require allocatable memory before imports.
    if available < 3 * 1024 ** 3 or psutil.virtual_memory().available < 512 * 1024 ** 2:
        raise MemoryError("E5 requires 3 GiB allocatable memory and 512 MiB free RAM")
    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(4)
    model = SentenceTransformer(os.getenv("E5_MODEL_PATH", "intfloat/multilingual-e5-base"),
                                revision=E5_REVISION, device="cpu", trust_remote_code=False)
    root = Path(os.getenv("CANDIDATE_RUNTIME_DIR", str(Path.home() / ".candidate-scoring")))
    cache = EmbeddingCache(model, os.getenv("E5_CACHE_DIR", str(root / "vectors")))
    payload = json.load(sys.stdin)
    vacancy = payload["vacancy"]
    query = "\n\n".join([vacancy.get("title", ""), " ".join(vacancy.get("required_skills", [])),
                            vacancy.get("description", "")])
    query_vectors = cache.encode(chunks(query, model.tokenizer, "query: "))
    scores = []
    for resume in payload["resumes"]:
        text = resume.get("anonymized_text", "")
        vectors = cache.encode(chunks(text, model.tokenizer, "passage: "))
        score = float(np.clip((query_vectors @ vectors.T).max(axis=1).mean(), -1, 1)) if len(vectors) and len(query_vectors) else None
        scores.append(score)
    print(json.dumps({"scores": scores, "cache_hits": cache.hits, "cache_misses": cache.misses,
                      "model_revision": E5_REVISION}))


if __name__ == "__main__":
    worker()
