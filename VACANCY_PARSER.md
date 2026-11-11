# Парсер вакансий для актуального main

Изменения подготовлены для коммита
`f72886fa53fc071f5d2de54fea3cea76eea6de5e` и API с модулями `api/services`.
Работа выполняется в ветке `feature/vacancy-parser`, созданной от main.
Папки из старого архива ветки Parser поверх main не копируются.

## Что изменено

- `Parser/vacancy_parser.py`, `vacancy_requirements.py`: названия, разделы,
  переносы, обязательность/желательность навыков, отрицания, JSON-словарь результата.
- `Parser/vacancy_input.py`: чтение PDF/TXT и очистка.
- `Parser/parse_vacancies.py`: отдельный запуск для файла, папки или ZIP.
- `scorer/expression_catalog.py`: расширен единый словарь, используемый также резюме.
- `api/services/documents.py`: чтение вакансий выполняется функцией
  `extract_vacancy_text`, а не функцией для резюме. Сохранение новых полей уже есть
  в актуальном main; старое `api/main.py` из ветки Parser не переносится.
- `requirements-parser.txt`, проверки парсера и обновлённые ожидания двух
  существующих тестов. Общие зависимости и формулы скоринга не заменяются.
- `.gitignore`: исключены локальные результаты и архив примеров.

Резюме, UI, модели, Neo4j, запускатели, структура API, формулы TF-IDF и рейтинга
сохраняются из main. Новых внешних зависимостей у парсера нет: нужен pypdf,
который уже указан в общих зависимостях.

## Запуск отдельно

В терминале корня репозитория Windows / PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-parser.txt
.\.venv\Scripts\python.exe -m Parser.parse_vacancies "C:\путь\к\vacancies.zip" -o result.json
```

Если среда уже настроена, достаточно последней команды.
PDF/TXT также можно передать напрямую, либо указать папку.
Результат — JSON с `vacancies`, `errors`, `duplicates` и `summary`.

```python
from Parser.vacancy_parser import parse_vacancy_bytes, parse_vacancy_text

parsed = parse_vacancy_text("Python developer\nТребования: Python, SQL")
data = parsed.to_dict()
```

## Проверка

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Тесты только парсера с минимальными зависимостями:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_vacancy_parser tests.test_vacancy_requirements tests.test_vacancy_ready -v
```

Для шести дополнительных проверок корпуса помести предоставленный архив
в `examples/vacancies.zip`. Архив не добавляется в Git; без него проверки
корпуса пропускаются. На предоставленных 55 PDF разбор выполнялся без ошибок.
Сохранение полей вакансии и их использование скорингом проверяются тестовыми
хранилищами; живые MongoDB, Neo4j и E5 отдельно не запускаются этими проверками.

## Сохранение в Git

Перед коммитом проверь `git status`, `git diff` и результаты тестов.
Добавляй конкретные файлы по задаче. Не включай `.venv`, `.env`, архивы,
модели, БД и `result.json`.

Пример сообщения: `feat(parser): add vacancy parsing`.
Отправка своей рабочей ветки: `git push -u origin feature/vacancy-parser`.
После отправки создаётся Pull Request в main команды.

## Ограничения

Поддерживаются машиночитаемые PDF и TXT. OCR по текущему заданию не нужен.
Неизвестные навыки остаются в тексте; для списков навыков используется конечный
словарь. Необычная вёрстка и сложные формулировки могут потребовать проверки.
У двух исходных вакансий отдельные обязанности не распознаны: поле пустое,
есть предупреждение. Доступность полей не означает полной точности на любых
новых документах. Правила подробно описаны в `Parser/VACANCY_RULES.md`.
