"""Conservative open-vocabulary identifiers in candidate requirement text.

Finds literal technical names, not inferred knowledge of related technologies.
The caller must assign a role; a company stack is never promoted by this code.
Unknown candidates retain evidence and are distinguished from catalog matches.
"""

import re

from scorer.professional_expressions import ExpressionMention, _with_polarity, canonical_skill_key
from scorer.skill_matching import skill_mentions

_TOKEN = re.compile(r"(?<![\w.])[A-Za-z][A-Za-z0-9_+#]*(?:[.\-][A-Za-z0-9_+#]+)*(?![\w+#])")
_STANDARD = re.compile(r"(?<!\w)(?:(?:ISO|IEC|IEEE|ГОСТ|МЭК)\s+\d[\w.\-]*(?:\s*[-–]\s*\d+)?|\d{2,5}[-‑]ФЗ)(?!\w)", re.I)
_QUALIFICATION = re.compile(
    r"\b(?:знани\w*|опыт\w*|владени\w*|умени\w*|понимани\w*|навык\w*|работ\w*|способност\w*|сертификат\w*|обязател\w*|необязател\w*|желател\w*|требу(?:ется|ются)|knowledge|experience|proficiency|skills?|familiar|required|mandatory|preferred|optional)\b",
    re.I,
)
_INSTRUCTION = re.compile(
    r"^(?:если\s+у\s+вас|прикреп\w*|присыла\w*|отправ\w*|отклик\w*|attach|apply|send\s+(?:us|your))\b", re.I
)
_STOP = set(
    """a an the and or but with without of for in on at by to from is are be have has
you your we our us it its as that this these those can must should will would do
what why how who when where work working experience experiences knowledge good
strong basic advanced proficiency skills skill required requirements preferred
optional nice plus other others relevant minimum years year months month level
junior middle senior lead developer development engineer engineering specialist
professional responsibilities qualifications technical technologies technology
tools tool programming languages language frameworks framework systems system
applications application services service software hardware full stack frontend
backend data business project projects product products remote office fulltime
certified certification administrator certified quality communication teamwork
support management team teams ability understanding familiar internship intern
test tests testing code coding using use related equivalent etc e.g i.e vs
no not never course training education learn learning candidates candidate
""".split()
)
_VENDORS = {"microsoft", "apache", "adobe", "google", "amazon"}


def discover_identifiers(text, catalog, *, backend="regex", context=""):
    """Return unregistered name spans, respecting known compound boundaries."""
    context = context or text
    if _INSTRUCTION.match(context.strip()):
        return []
    tokens = list(_TOKEN.finditer(text))
    if not _QUALIFICATION.search(context):
        # A plain technical list is accepted, English narrative is not.
        residual = _TOKEN.sub("", text)
        if re.search(r"[а-яё]", residual, re.I) or any(m.group().casefold() in _STOP for m in tokens):
            return []
    known = skill_mentions(text, catalog, backend=backend)
    spans = [(m.start, m.end) for m in known]
    candidates = []
    for match in [*_STANDARD.finditer(text), *tokens]:
        name = match.group()
        if name.casefold() in _STOP or len(name) > 70:
            continue
        if name.casefold() in _VENDORS or any(
            len(name) >= 5 and vendor.startswith(name.casefold()) for vendor in _VENDORS
        ):
            continue
        # Tools such as aiohttp, pydantic or poetry are commonly lowercase.
        # Russian competence prose supplies an explicit context; ordinary
        # English narrative still needs a distinctive identifier spelling.
        russian_qualification = bool(re.search(r"[а-яё]", context, re.I) and _QUALIFICATION.search(context))
        if not any(char.isupper() or char.isdigit() for char in name) and not russian_qualification:
            continue
        if len(name) == 1 and name not in {"C", "R"}:
            continue
        if any(left < match.end() and match.start() < right for left, right in spans):
            continue
        candidates.append((match.start(), match.end(), name))
    # Combine adjacent title-cased product words. Two bare acronyms remain
    # separate unless a standard pattern already covered their numeric suffix.
    combined = []
    for start, end, name in sorted(candidates, key=lambda value: (value[0], value[0] - value[1])):
        if combined:
            left, previous_end, previous = combined[-1]
            gap = text[previous_end:start]
            if (
                gap.isspace()
                and start >= previous_end
                and not (previous.isupper() and name.isupper())
                and len((previous + " " + name).split()) <= 4
            ):
                combined[-1] = (left, end, previous + " " + name)
                continue
        if not combined or start >= combined[-1][1]:
            combined.append((start, end, name))
    mentions = [
        ExpressionMention(canonical_skill_key(name), name, "skill", text[start:end], start, end)
        for start, end, name in combined
    ]
    return [m for m in _with_polarity(text, mentions) if not m.negated or m.negation_kind == "requirement"]


def discover_skills(text, catalog, *, backend="regex", context="", include_phrases=False):
    identifiers = discover_identifiers(text, catalog, backend=backend, context=context)
    if not include_phrases or _INSTRUCTION.match((context or text).strip()):
        return identifiers
    from Parser.vacancy_phrases import discover_phrases

    return [*identifiers, *discover_phrases(text, catalog, backend=backend)]
