"""Public request/response contracts for backend API v4."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from scorer.skill_matching import canonical_skill_key

Identifier = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[\w][\w.:-]*$")
]
Skill = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
SkillList = Annotated[list[Skill], Field(max_length=500)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def normalize_skills(values: list[str]) -> list[str]:
    names = {}
    for value in values:
        name = " ".join(value.split())
        names.setdefault(canonical_skill_key(name), name)
    return list(names.values())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VacancyCreate(StrictModel):
    id: Identifier | None = None
    title: Title
    description: str = Field(default="", max_length=20000)
    requirements: str = Field(default="", max_length=12000)
    responsibilities: str = Field(default="", max_length=12000)
    required_skills: SkillList = Field(default_factory=list)
    preferred_skills: SkillList = Field(default_factory=list)
    critical_skills: SkillList = Field(default_factory=list)

    @field_validator("required_skills", "preferred_skills", "critical_skills")
    @classmethod
    def normalize(cls, value):
        return normalize_skills(value)

    @model_validator(mode="after")
    def check_critical(self):
        required = {canonical_skill_key(s) for s in self.required_skills}
        if any(canonical_skill_key(s) not in required for s in self.critical_skills):
            raise ValueError("Критические навыки должны входить в обязательные.")
        return self


class VacancyPatch(StrictModel):
    title: Title | None = None
    description: str | None = Field(default=None, max_length=20000)
    requirements: str | None = Field(default=None, max_length=12000)
    responsibilities: str | None = Field(default=None, max_length=12000)
    required_skills: SkillList | None = None
    preferred_skills: SkillList | None = None
    critical_skills: SkillList | None = None

    @model_validator(mode="after")
    def reject_null(self):
        if not self.model_fields_set or any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Передайте хотя бы одно непустое поле; null не поддерживается.")
        return self


class ResumePatch(StrictModel):
    hidden: bool = Field(strict=True)


class ScoringRequest(StrictModel):
    vacancy_id: Identifier
    resume_ids: Annotated[list[Identifier], Field(min_length=1, max_length=500)] | None = None
    coverage_weight: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False, strict=True)
    critical_multiplier: float | None = Field(default=None, gt=1, le=100, allow_inf_nan=False, strict=True)

    @field_validator("resume_ids")
    @classmethod
    def unique_ids(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("resume_ids содержит повторы.")
        return value


class FeedbackInput(StrictModel):
    run_id: Identifier
    resume_id: Identifier
    label: Literal["relevant", "irrelevant"]
    reviewer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    comment: str = Field(default="", max_length=2000)
    event_id: Identifier | None = None


class FeedbackEvent(BaseModel):
    event_id: str
    run_id: str
    vacancy_id: str
    resume_id: str
    label: Literal["relevant", "irrelevant"]
    reviewer: str | None = None
    comment: str = ""
    created_at: datetime


class CandidateResult(BaseModel):
    resume_id: str
    candidate: str
    file_number: int
    file_type: str
    score: float = Field(ge=0, le=100)
    skill_coverage: float | None = Field(default=None, ge=0, le=100)
    text_similarity: float | None = Field(default=None, ge=0, le=100)
    matched_skills: list[str]
    missing_skills: list[str]
    matched_critical_skills: list[str] = Field(default_factory=list)
    missing_critical_skills: list[str] = Field(default_factory=list)
    ranking_version: str


class ScoringRun(BaseModel):
    run_id: str
    vacancy_id: str
    vacancy_title: str
    created_at: datetime
    ranking_version: str
    parameters: dict[str, Any]
    corpus: dict[str, Any]
    results: list[CandidateResult]
    resume_count: int
    duration_seconds: float
    status: Literal["completed"]


class LegacyRun(BaseModel):
    run_id: str
    vacancy_id: str
    created_at: datetime
    ranking_version: str = "legacy"
    results: list[dict[str, Any]]
    resume_count: int = 0
    status: Literal["completed"] = "completed"


class RunSummary(BaseModel):
    run_id: str | None = None
    vacancy_id: str
    created_at: datetime
    ranking_version: str = "legacy"
    resume_count: int = 0
    status: str = "completed"


class VacancyDocument(BaseModel):
    id: str
    title: str
    description: str = ""
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    critical_skills: list[str] = Field(default_factory=list)
    requirements: str = ""
    responsibilities: str = ""
    parse_details: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class ResumeDocument(BaseModel):
    resume_id: str
    file_type: str = "pdf"
    title: str = ""
    experience: str = ""
    education: str = ""
    skills: list[str] = Field(default_factory=list)
    anonymized_text: str = ""
    parse_details: dict[str, Any] = Field(default_factory=dict)
    hidden: bool = False


class FileError(BaseModel):
    file_type: str
    error: str
    file_number: str | None = None


class ResumeBatch(BaseModel):
    resumes: list[ResumeDocument]
    count: int
    errors: list[FileError]
