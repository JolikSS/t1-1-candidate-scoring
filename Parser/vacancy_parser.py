"""Extract role requirements and skills from text-based vacancy documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from scorer.skill_matching import find_skill_mentions


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


EMAIL_URL = re.compile(r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|https?://\S+|www\.\S+)", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{7,}\d)(?!\w)")
COMPANY_LINE = re.compile(
    r"^(?:ООО|ОАО|ЗАО|ПАО|АО|ИП)\s+",
    re.IGNORECASE,
)
SECTION_ALIASES = {
    "requirements": (
        "требования", "мы ожидаем", "что нужно", "квалификация", "requirements", "what you need",
    ),
    "responsibilities": (
        "обязанности", "задачи", "что делать", "responsibilities", "what you will do",
    ),
    "conditions": ("условия", "мы предлагаем", "условия работы", "conditions", "we offer"),
    "stop": (
        "контакты", "как откликнуться", "работодатель", "компания", "адрес", "телефон",
        "о компании", "contact", "about company",
    ),
}
HEADINGS = {
    name: re.compile(
        r"(?:" + "|".join(re.escape(alias) for alias in aliases)
        + r")\s*(?:[:：]\s*(?P<content>.*))?",
        re.IGNORECASE,
    )
    for name, aliases in SECTION_ALIASES.items()
}


def _extract_sections(lines: list[str]) -> dict[str, list[str]]:
    """Collect section content until the next recognized heading.

    Inline content requires a colon so ordinary sentences starting with a
    heading word do not accidentally switch sections. Stop headings discard
    their own content and following lines until another section starts.
    """
    sections: dict[str, list[str]] = {"requirements": [], "responsibilities": [], "conditions": []}
    active = ""
    for line in lines:
        for name, pattern in HEADINGS.items():
            heading = pattern.fullmatch(line)
            if heading:
                active = "" if name == "stop" else name
                line = heading.group("content") or ""
                break
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
    # Job titles usually lead these exports. Skip dates, salaries, contact lines.
    title = "Вакансия"
    for line in lines[:15]:
        clean = EMAIL_URL.sub(" ", PHONE.sub(" ", line)).strip(" -–—:|")
        if COMPANY_LINE.match(clean):
            continue
        if (3 <= len(clean) <= 140 and not re.search(r"\d{2,}|руб|грн|зарплат|зарплата|опубликован|обновлен", clean, re.I)
                and not re.search(r"@|https?://|\b(?:город|адрес|телефон|контакт)", clean, re.I)):
            title = clean
            break
    sections = _extract_sections(lines)
    requirements = " ".join(sections["requirements"])
    responsibilities = " ".join(sections["responsibilities"])
    description = " ".join(filter(None, (requirements, responsibilities, " ".join(sections["conditions"]))))
    # Only explicit requirements supply mandatory skills. Keep their line
    # boundaries so a negation on one line does not affect the next one.
    skills = find_skill_mentions("\n".join(sections["requirements"]), SKILL_CATALOG)
    return ParsedVacancy(title[:200], requirements[:12000], responsibilities[:12000], description[:20000], skills)
