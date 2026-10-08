"""Neo4j persistence for vacancies and resume scoring results."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from neo4j import Driver, GraphDatabase


class GraphStore:
    """Store vacancies, candidates, resumes, skills, and score relationships."""

    def __init__(self, uri: str, username: str, password: str) -> None:
        self.driver: Driver = GraphDatabase.driver(uri, auth=(username, password))

    def verify_and_prepare(self) -> None:
        self.driver.verify_connectivity()
        constraints = (
            "CREATE CONSTRAINT vacancy_id_unique IF NOT EXISTS "
            "FOR (node:Vacancy) REQUIRE node.id IS UNIQUE",
            "CREATE CONSTRAINT candidate_id_unique IF NOT EXISTS "
            "FOR (node:Candidate) REQUIRE node.id IS UNIQUE",
            "CREATE CONSTRAINT resume_id_unique IF NOT EXISTS "
            "FOR (node:Resume) REQUIRE node.id IS UNIQUE",
            "CREATE CONSTRAINT skill_name_unique IF NOT EXISTS "
            "FOR (node:Skill) REQUIRE node.name IS UNIQUE",
        )
        with self.driver.session() as session:
            for constraint in constraints:
                session.run(constraint).consume()

    def close(self) -> None:
        self.driver.close()

    def list_vacancies(self) -> list[dict[str, Any]]:
        query = (
            "MATCH (vacancy:Vacancy) "
            "OPTIONAL MATCH (vacancy)-[:REQUIRES]->(skill:Skill) "
            "RETURN vacancy.id AS id, vacancy.title AS title, "
            "vacancy.description AS description, "
            "collect(skill.name) AS required_skills "
            "ORDER BY vacancy.title, vacancy.id"
        )
        with self.driver.session() as session:
            return [record.data() for record in session.run(query)]

    def upsert_vacancy(
        self,
        vacancy_id: str,
        title: str,
        required_skills: list[str],
        description: str = "",
    ) -> dict[str, Any]:
        query = (
            "MERGE (vacancy:Vacancy {id: $vacancy_id}) "
            "SET vacancy.title = $title, vacancy.description = $description "
            "WITH vacancy "
            "OPTIONAL MATCH (vacancy)-[old:REQUIRES]->(:Skill) "
            "DELETE old "
            "WITH DISTINCT vacancy "
            "FOREACH (skill_name IN $skills | "
            "MERGE (skill:Skill {name: skill_name}) "
            "MERGE (vacancy)-[:REQUIRES]->(skill)) "
            "RETURN vacancy.id AS id, vacancy.title AS title, "
            "vacancy.description AS description, "
            "$skills AS required_skills"
        )
        with self.driver.session() as session:
            record = session.run(
                query,
                vacancy_id=vacancy_id,
                title=title,
                skills=required_skills,
                description=description,
            ).single()
        return record.data()

    def save_score(
        self,
        *,
        candidate_id: str,
        resume_id: str,
        file_type: str,
        skills: list[str],
        vacancy_id: str,
        score: float,
        text_similarity: float | None,
        matched_skills: list[str],
        missing_skills: list[str],
    ) -> None:
        scored_at = datetime.now(timezone.utc).isoformat()
        query = (
            "MATCH (vacancy:Vacancy {id: $vacancy_id}) "
            "MERGE (candidate:Candidate {id: $candidate_id}) "
            "CREATE (resume:Resume {id: $resume_id, file_type: $file_type, "
            "created_at: $scored_at}) "
            "CREATE (candidate)-[:HAS_RESUME]->(resume) "
            "FOREACH (skill_name IN $skills | "
            "MERGE (skill:Skill {name: skill_name}) "
            "MERGE (resume)-[:HAS_SKILL]->(skill)) "
            "CREATE (resume)-[:SCORED_FOR {score: $score, "
            "text_similarity: $text_similarity, "
            "matched_skills: $matched_skills, missing_skills: $missing_skills, "
            "scored_at: $scored_at}]->(vacancy)"
        )
        with self.driver.session() as session:
            session.run(
                query,
                candidate_id=candidate_id,
                resume_id=resume_id,
                file_type=file_type,
                skills=skills,
                vacancy_id=vacancy_id,
                score=score,
                text_similarity=text_similarity,
                matched_skills=matched_skills,
                missing_skills=missing_skills,
                scored_at=scored_at,
            ).consume()

    def list_results(self, vacancy_id: str) -> list[dict[str, Any]]:
        query = (
            "MATCH (candidate:Candidate)-[:HAS_RESUME]->(resume:Resume) "
            "-[evaluation:SCORED_FOR]->(vacancy:Vacancy {id: $vacancy_id}) "
            "RETURN candidate.id AS candidate_id, "
            "resume.id AS resume_id, "
            "evaluation.score AS score, "
            "evaluation.text_similarity AS text_similarity, "
            "evaluation.matched_skills AS matched_skills, "
            "evaluation.missing_skills AS missing_skills, "
            "evaluation.scored_at AS scored_at "
            "ORDER BY evaluation.score DESC, candidate.id, resume.id"
        )
        with self.driver.session() as session:
            results = [record.data() for record in session.run(query, vacancy_id=vacancy_id)]
        for result in results:
            result["candidate"] = f"Кандидат {result.pop('candidate_id')[-6:]}"
        return results
