"""Extract role requirements and skills from text-based vacancy documents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from Parser.document_structure import COMPANY as COMPANY_LINE, compile_headings, match_heading, select_job_title
from Parser.vacancy_requirements import classify_vacancy_skills


class VacancyParseError(ValueError):
    pass


SKILL_CATALOG = [
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", ".NET", "SQL", "PostgreSQL", "MySQL",
    "MongoDB", "Neo4j", "Linux", "Windows Server", "Docker", "Kubernetes", "Git", "FastAPI", "Django",
    "Flask", "React", "Angular", "Vue", "Node.js", "REST API", "GraphQL", "Redis", "Kafka", "RabbitMQ",
    "AWS", "Azure", "GCP", "Terraform", "Ansible", "Jenkins", "GitLab CI", "CI/CD", "HTML", "CSS",
    "PowerShell", "Bash", "VMware", "VMware ESXi", "MS Exchange", "Active Directory", "TCP/IP", "DNS",
    "PostgreSQL", "Pandas", "NumPy", "scikit-learn", "TensorFlow", "PyTorch", "Excel", "Power BI",
    "Tableau", "Agile", "Scrum", "1C", "SAP", "English", "Ukrainian", "Russian",
]


@dataclass(frozen=True)
class ParsedVacancy:
    title: str
    requirements: str
    responsibilities: str
    description: str
    required_skills: list[str]
    preferred_skills: list[str] = field(default_factory=list)
    parse_details: dict = field(default_factory=dict)


EMAIL_URL = re.compile(r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|https?://\S+|www\.\S+)", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
SECTION_ALIASES = {
    "requirements": (
        "требования", "мы ожидаем", "что нужно", "квалификация", "requirements", "what you need",
        "обязательные требования", "обязательные навыки", "must have", "must-have", "required qualifications",
        "требования к квалификации", "что для этого требуется", "что обязательно", "что мы ищем в кандидате",
    ),
    "preferred": (
        "будет плюсом", "будут плюсом", "будет преимуществом", "желательно",
        "желательные навыки", "nice to have", "nice-to-have", "preferred qualifications", "optional",
    ),
    "responsibilities": (
        "обязанности", "задачи", "что делать", "responsibilities", "what you will do",
        "что нужно делать", "что предстоит делать", "что предстоит сделать", "чем предстоит заниматься",
        "задачи, которые предстоит выполнять", "основные задачи", "ключевые задачи",
    ),
    "conditions": (
        "условия", "мы предлагаем", "условия работы", "conditions", "we offer",
        "что вы получите", "условия оформления", "условия работы и компенсация",
    ),
    "context": (
        "наш стек", "стек технологий", "что мы используем", "что мы делаем", "что уже есть",
        "ваши личные качества (soft skills)", "о вакансии", "описание вакансии",
    ),
    "stop": (
        "контакты", "как откликнуться", "работодатель", "компания", "адрес", "телефон",
        "о компании", "contact", "about company",
    ),
}
HEADING_VARIANTS = {
    "requirements": (
        r"(?:основные\s+)?требования\s+к\s+(?:кандидату|соискателю)",
        r"(?:что\s+)?мы\s+(?:ожидаем|жд[её]м)(?:\s+от\s+(?:вас|кандидата|тебя))?",
        r"что\s+(?:ожидаем|жд[её]м)\s+от\s+(?:вас|кандидата|тебя)",
        r"наши\s+ожидания(?:\s+от\s+кандидата|\s*\(требования\s+к\s+кандидату\))?",
    ),
    "responsibilities": (r"чем\s+занимается\s+(?:наша\s+)?[\w-]*команда",),
    "conditions": (r"(?:что\s+)?мы\s+(?:предлагаем|готовы\s+предложить)",),
}
HEADINGS = compile_headings(SECTION_ALIASES, HEADING_VARIANTS)


def _extract_sections(lines: list[str]) -> dict[str, list[str]]:
    """Collect section content until the next recognized heading.

    Inline content requires a colon so ordinary sentences starting with a
    heading word do not accidentally switch sections. Stop headings discard
    their own content and following lines until another section starts.
    """
    sections: dict[str, list[str]] = {name: [] for name in SECTION_ALIASES if name != "stop"}
    active = ""
    for line in lines:
        heading = match_heading(line, HEADINGS)
        if heading:
            active = "" if heading.section == "stop" else heading.section
            line = heading.content
        if not active or not line:
            continue
        line = EMAIL_URL.sub(" ", PHONE.sub(" ", line)).strip(" -–—:|")
        if line and not re.search(r"\b(?:адрес|телефон|контакт|зарплатная карта)\b", line, re.I):
            sections[active].append(line)
    return sections


def parse_vacancy_bytes(file_name: str, content: bytes) -> ParsedVacancy:
    if Path(file_name).suffix.casefold() == ".txt":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("cp1251", errors="replace")
    elif Path(file_name).suffix.casefold() == ".pdf":
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted:
                raise VacancyParseError("PDF защищён паролем.")
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except VacancyParseError:
            raise
        except Exception as exc:
            raise VacancyParseError("Не удалось прочитать PDF вакансии.") from exc
    else:
        raise VacancyParseError("Поддерживаются PDF и TXT.")
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    if not lines:
        raise VacancyParseError("Не найден текстовый слой; OCR не поддерживается.")
    title, title_details, title_warnings = select_job_title(lines, HEADINGS, known_terms=SKILL_CATALOG)
    sections = _extract_sections(lines)
    requirements = " ".join(sections["requirements"])
    responsibilities = " ".join(sections["responsibilities"])
    description = " ".join(filter(None, (
        requirements, " ".join(sections["preferred"]), responsibilities,
        " ".join(sections["context"]), " ".join(sections["conditions"]),
    )))
    classification = classify_vacancy_skills(sections["requirements"], sections["preferred"], SKILL_CATALOG)
    warnings = [*title_warnings, *classification.warnings]
    if not requirements:
        warnings.append("missing_requirements")
    if not responsibilities:
        warnings.append("missing_responsibilities")
    if not description:
        warnings.append("empty_professional_text")
    return ParsedVacancy(
        title=title or "Вакансия", requirements=requirements[:12000], responsibilities=responsibilities[:12000],
        description=description[:20000], required_skills=classification.required_skills,
        preferred_skills=classification.preferred_skills,
        parse_details={"parser_version": "vacancy-rules-1.1", "skill_evidence": classification.evidence,
                       "title_selection": title_details, "warnings": warnings},
    )
