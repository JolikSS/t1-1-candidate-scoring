import tempfile
import unittest
from pathlib import Path
import numpy as np
from tools.resume_experiments import chunks, aggregate, EmbeddingCache, validate_llm_output, llm_extract, require_memory


class Tokenizer:
    def encode(self, text, add_special_tokens=False, **_):
        values = [int(x) if x.isdigit() else -1 for x in text.split()]
        return values + ([-2,-3] if add_special_tokens else [])
    def decode(self, tokens, **_): return " ".join(map(str,tokens))


class Model:
    def __init__(self): self.calls=0
    def encode(self,texts,**_):
        self.calls+=1
        return np.array([[1.0]+[0.0]*767 for _ in texts],dtype=np.float32)


class ExperimentTests(unittest.TestCase):
    def test_long_chunks_cover_all_tokens_and_prefix(self):
        parts=chunks(" ".join(map(str,range(1600))),Tokenizer(),"passage: ")
        covered=set()
        for part in parts:
            self.assertTrue(part.startswith("passage: "))
            self.assertLessEqual(len(Tokenizer().encode(part,True)),512)
            covered.update(map(int,part.split()[1:]))
        self.assertEqual(covered,set(range(1600)))
        self.assertEqual(chunks("",Tokenizer(),"query: "),[])
        with self.assertRaises(ValueError): chunks("text",Tokenizer(),"wrong")

    def test_cache_revision_prefix_content_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            model=Model();cache=EmbeddingCache(model,directory)
            first=cache.encode(["query: text"])
            self.assertTrue(np.array_equal(first,cache.encode(["query: text"])))
            self.assertEqual(model.calls,1)
            cache.encode(["passage: text","query: changed"])
            self.assertEqual(model.calls,2)
            EmbeddingCache(model,directory,"other-revision").encode(["query: text"])
            self.assertEqual(model.calls,3)
            for p in Path(directory).glob("*.npy"):p.write_bytes(b"broken")
            cache.encode(["query: text"])
            self.assertEqual(model.calls,4)

    def test_aggregation_and_empty(self):
        q=np.array([[1.,0.],[0.,1.]])
        self.assertEqual(aggregate(q,np.array([[1.,0.]])),{"max":1.0,"mean_best":0.5})
        self.assertIsNone(aggregate([],q)["max"])

    def test_llm_schema_grounding_and_personal_data(self):
        source="Python services\nEmail: a@example.org"
        fields,rejected=validate_llm_output({"fields":[
            {"section":"experience","text":"Python services","evidence":"Python services"},
            {"section":"skills","text":"Docker","evidence":"Docker"},
            {"section":"summary","text":"Email: a@example.org","evidence":"Email: a@example.org"}]},source)
        self.assertEqual(len(fields),1);self.assertEqual(rejected,2)
        with self.assertRaises(ValueError):validate_llm_output([],source)
        with self.assertRaises(ValueError):validate_llm_output({"fields":"bad"},source)

    def test_llm_long_input_resources_and_external_endpoint_rejected(self):
        with self.assertRaises(ValueError): llm_extract("data", "https://example.org")
        with self.assertRaises(ValueError): llm_extract("x"*100001)
        with self.assertRaises(MemoryError): require_memory(100, 200)
        require_memory(200, 200)
