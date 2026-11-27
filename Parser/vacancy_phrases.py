"""Bounded Russian noun phrases in candidate requirements, with evidence.

This is dictionary morphology plus grammar rules, not a trained NER model.
Only technical heads/modifiers are accepted; employer prose is not an input.
Unregistered phrases must remain distinguishable from reviewed catalog skills.
"""

import re
from functools import lru_cache

from scorer.professional_expressions import ExpressionMention, _with_polarity, canonical_skill_key
from scorer.skill_matching import skill_mentions

_WORD = re.compile(r"[а-яё]+(?:[-‑][а-яё]+)*", re.I)
_HEADS = set(
    """программирование проектирование администрирование интеграция
тестирование автоматизация оптимизация моделирование анализ обработка
архитектура протокол технология теория алгоритм структура интерфейс
электроника электротехника схемотехника механика аэродинамика
диагностика настройка конфигурация разработка управление безопасность
виртуализация маршрутизация коммутация мониторинг логирование
документирование отладка профилирование рефакторинг
""".split()
)
_MODIFIERS = re.compile(
    r"^(?:программн|техническ|информационн|сетев|вычислительн|операционн|"
    r"распределенн|реляционн|объектн|клиент|серверн|системн|прикладн|"
    r"функциональн|интеграционн|асинхронн|многопоточн|автоматическ|"
    r"статистическ|математическ|компьютерн|инженерн|электронн|электрическ)"
)
_GENERIC = set(
    """опыт знание умение понимание навык способность требование
год лет месяц работа кандидат сотрудник команда компания образование
основа принцип инструмент продукт проект задача решение область сфера
уровень качество возможность желание интерес готовность условие
""".split()
)
_GENERIC.update("разработчик программист инженер специалист направление образование наука".split())
_DECORATIVE = set(
    "коммерческий практический уверенный глубокий хороший базовый отличный современный новый различный собственный необходимый большой сложный".split()
)
_SINGLE = set(
    "электроника электротехника схемотехника аэродинамика механика математика криптография виртуализация маршрутизация коммутация программирование профилирование отладка диагностика".split()
)
_HEADS.update(
    "построение вычисление расчёт система производство защита эксплуатация развёртывание восстановление обслуживание внедрение сервис взаимодействие передача транспорт хранение связь сеть сборка сопровождение".split()
)


@lru_cache(maxsize=1)
def _morph():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=50000)
def _parse(word):
    return _morph().parse(word.casefold().replace("ё", "е"))[0]


def _name(tokens, parses, head):
    """Inflect only the head group, preserving dependent genitives."""
    values = [token.group().casefold() for token in tokens]
    head_parse = parses[head]
    grammemes = {"nomn", head_parse.tag.number or "sing"}
    if head_parse.tag.number != "plur" and head_parse.tag.gender:
        grammemes.add(head_parse.tag.gender)
    for i in range(head + 1):
        inflected = parses[i].inflect(grammemes)
        values[i] = inflected.word if inflected else parses[i].normal_form
    value = " ".join(values)
    return value[:1].upper() + value[1:]


_HEADS.update(
    "язык протокол сеть метод модель база платформа технология архитектура проектирование программирование реализация конфигурация сопровождение".split()
)


def discover_phrases(text, catalog, *, backend="regex"):
    known = [(m.start, m.end) for m in skill_mentions(text, catalog, backend=backend)]
    words = list(_WORD.finditer(text))
    parses = [_parse(word.group()) for word in words]
    result = []
    consumed = 0
    for offset, token in enumerate(words):
        if token.start() < consumed:
            continue
        if parses[offset].normal_form in _DECORATIVE:
            continue
        start = offset
        head = offset
        while head < min(len(words), start + 3) and parses[head].tag.POS == "ADJF":
            if head > start and not text[words[head - 1].end() : words[head].start()].isspace():
                break
            head += 1
        if head >= len(words) or parses[head].tag.POS != "NOUN":
            continue
        root = parses[head].normal_form
        if root in _GENERIC or not (root in _HEADS or any(_MODIFIERS.match(p.normal_form) for p in parses[start:head])):
            continue
        end = head + 1
        # A noun can have one/two dependent genitive noun groups. Do not cross
        # punctuation, prepositions, verbs or arbitrary coordinated lists.
        while end < min(len(words), start + 7):
            if not text[words[end - 1].end() : words[end].start()].isspace():
                break
            p = parses[end]
            if p.tag.POS not in {"ADJF", "NOUN"} or p.tag.case != "gent" or p.normal_form in _GENERIC:
                break
            end += 1
        if (end - start < 2 and root not in _SINGLE) or parses[end - 1].tag.POS != "NOUN":
            continue
        left, right = words[start].start(), words[end - 1].end()
        if any(a < right and left < b for a, b in known):
            continue
        name = _name(words[start:end], parses[start:end], head - start)
        result.append(
            ExpressionMention(canonical_skill_key(name), name, "candidate_phrase", text[left:right], left, right)
        )
        consumed = right
    return [m for m in _with_polarity(text, result) if not m.negated or m.negation_kind == "requirement"]
