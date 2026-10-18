"""Materialize each run atomically, then rank using skill paths in Neo4j."""
from __future__ import annotations
import os
from neo4j import GraphDatabase
from scorer.skill_matching import canonical_skill_key


RESULT_QUERY = """
    MATCH (run:ScoringRun {id:$run_id})-[e:EVALUATES]->(resume:Resume)
    MATCH (run)-[:OBSERVED]->(snapshot:SkillSnapshot)-[:OF_RESUME]->(resume)
    OPTIONAL MATCH (run)-[requirement:REQUIRES]->(skill:Skill)
    WITH resume,e,snapshot,requirement,skill ORDER BY skill.id
    WITH resume,e,snapshot,collect({key:skill.id,name:requirement.name}) AS requirements
    WITH resume,e,
         [r IN requirements WHERE r.key IS NOT NULL AND EXISTS { MATCH (snapshot)-[:HAS_SKILL]->(:Skill {id:r.key}) } | r.name] AS matched,
         [r IN requirements WHERE r.key IS NOT NULL AND NOT EXISTS { MATCH (snapshot)-[:HAS_SKILL]->(:Skill {id:r.key}) } | r.name] AS missing
    RETURN resume.id AS resume_id,e.candidate AS candidate,e.file_type AS file_type,
           e.file_number AS file_number,e.text_similarity AS text_similarity,
           e.semantic_similarity AS semantic_similarity,e.hybrid_score AS hybrid_score,
           matched AS matched_skills,missing AS missing_skills,
           CASE WHEN size(matched)+size(missing)=0 THEN 0.0
                ELSE 100.0*size(matched)/(size(matched)+size(missing)) END AS score
    ORDER BY score DESC,hybrid_score DESC,resume_id
"""

class GraphStore:
    def __init__(self):
        self.driver = GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.environ["NEO4J_PASSWORD"]),
            connection_timeout=5)

    def verify_and_prepare(self):
        self.driver.verify_connectivity()
        with self.driver.session() as session:
            for label in ("Vacancy", "Resume", "Skill", "ScoringRun", "SkillSnapshot"):
                session.run(f"CREATE CONSTRAINT {label.lower()}_id_unique IF NOT EXISTS "
                            f"FOR (n:{label}) REQUIRE n.id IS UNIQUE").consume()

    def close(self):
        self.driver.close()

    def rank(self, run_id, vacancy, resumes, metrics):
        required = [{"id": canonical_skill_key(s), "name": s} for s in vacancy["required_skills"]]
        rows = [{**metric, "skills": list(dict.fromkeys(canonical_skill_key(s) for s in resume.get("skills", [])))}
                for resume, metric in zip(resumes, metrics)]
        def materialize(tx):
            tx.run("MERGE (v:Vacancy {id:$vacancy_id}) SET v.title=$title "
                   "MERGE (run:ScoringRun {id:$run_id}) MERGE (run)-[:FOR_VACANCY]->(v)",
                   vacancy_id=vacancy["id"], title=vacancy["title"], run_id=run_id).consume()
            # Requirements are run-scoped so later vacancy edits cannot alter history.
            tx.run("MATCH (run:ScoringRun {id:$run_id}) "
                   "UNWIND $skills AS skill MERGE (s:Skill {id:skill.id}) "
                   "MERGE (run)-[r:REQUIRES]->(s) SET r.name=skill.name",
                   run_id=run_id, skills=required).consume()
            tx.run("MATCH (run:ScoringRun {id:$run_id}) UNWIND $rows AS row "
                   "MERGE (resume:Resume {id:row.resume_id}) "
                   "MERGE (run)-[e:EVALUATES]->(resume) "
                   "SET e += row{.candidate,.file_type,.file_number,.text_similarity,.semantic_similarity,.hybrid_score} "
                   "FOREACH (skill IN row.skills | MERGE (s:Skill {id:skill}) "
                   "MERGE (resume)-[:HAS_SKILL]->(s))",
                   run_id=run_id, rows=rows).consume()
            tx.run("UNWIND $rows AS row MATCH (resume:Resume {id:row.resume_id}) "
                   "OPTIONAL MATCH (resume)-[old:HAS_SKILL]->(skill:Skill) "
                   "WHERE NOT skill.id IN row.skills DELETE old", rows=rows).consume()
            # Snapshot observed skills per evaluation, retaining history on reparse.
            tx.run("MATCH (run:ScoringRun {id:$run_id}) UNWIND $rows AS row "
                   "MATCH (resume:Resume {id:row.resume_id}) "
                   "MERGE (observation:SkillSnapshot {id:$run_id + ':' + row.resume_id}) "
                   "MERGE (run)-[:OBSERVED]->(observation) MERGE (observation)-[:OF_RESUME]->(resume) FOREACH (key IN row.skills | MERGE (s:Skill {id:key}) MERGE (observation)-[:HAS_SKILL]->(s))",
                   run_id=run_id, rows=rows).consume()
            return [record.data() for record in tx.run(RESULT_QUERY, run_id=run_id)]
        with self.driver.session() as session:
            return session.execute_write(materialize)

    def list_results(self, run_id):
        with self.driver.session() as session:
            return session.execute_read(lambda tx: [record.data() for record in tx.run(RESULT_QUERY, run_id=run_id)])
