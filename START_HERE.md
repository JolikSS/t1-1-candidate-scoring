# Запуск обновления

## Windows, Python 3.13

1. Распакуйте архив отдельно и откройте `t1-1-candidate-scoring-skills` в VS Code.
2. В терминале PowerShell, в корне проекта:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-lock-windows-py313.txt
.\.venv\Scripts\python.exe -m streamlit run check_skills.py
```

Это проверка навыков без MongoDB, Neo4j и API. Активация окружения не требуется.
Для другого Python установите точные прямые зависимости:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt -r requirements-skills.txt
```

Для Linux/Python 3.12 есть отдельный `requirements-lock-linux-py312.txt`.
Windows lock разрешён для Windows/Python 3.13; нативный запуск Windows здесь не проверялся.
Чистое окружение, тесты и реальная Neo4j проверены на Linux/Python 3.12.

## Как посчитать recall

В браузере:

1. Загрузите `Требования JSON.zip` в поле эталонных навыков.
2. Загрузите `new_resume.zip` в поле документов. Будут выбраны только документы,
   для которых загружен эталон; 50 резюме не будут автоматически считаться вакансиями.
3. Просмотрите таблицу ожидаемых навыков. Для пяти знакомых JSON есть начальная
   ручная разметка предложений. Её можно исправлять; неизвестные словарю навыки
   нельзя удалять ради повышения оценки.
4. Нажмите **Рассчитать recall и precision**. Будут общие метрики, показатели
   каждого документа, ошибки категорий, пропуски и скачивание отчёта JSON.

Для собственного простого эталона подойдёт `["Python", "Docker", "НеизвестныйНавык"]`.
Можно выбрать «Вакансия» или «Резюме». Для пакета нужны объекты с `vacancy_id`
или `resume_id`, `source_file` и `required_skills`/`preferred_skills` или `skills`.
Навыки в массиве должны быть понятиями. Полные предложения перед расчётом
нужно разметить: `;` = отдельные понятия, `|` = допустимые альтернативы.

Эта же страница доступна в основном интерфейсе: раздел «Проверка навыков».
Чтобы считать recall резюме, нужен отдельный эталон навыков резюме.
JSON требований вакансии не является эталоном содержания резюме.

## Neo4j и VS Code

В VS Code: Ctrl+Shift+P → **Python: Select Interpreter** → Python из `.venv`.
Проверка импорта и версий:

```powershell
.\.venv\Scripts\python.exe -m tools.check_environment
```

Драйвер — `neo4j==5.28.8`, импорт — `from neo4j import GraphDatabase`.
Для работы с базой также требуется запущенный сервер, `NEO4J_ENABLED=true`
и параметры в `.env`; пример есть в `.env.example`.
Проверка реального подключения: та же команда с `--connect-neo4j`.
Не называйте свой файл или папку `neo4j`: это затенит установленную библиотеку.
В режиме без графа отсутствие драйвера больше не ломает импорт приложения.

## Проверки

```powershell
.\.venv\Scripts\python.exe tools/check_backend.py
```

10 дополнительных корпусных тестов используют прежний `examples/vacancies.zip`
с 55 вакансиями. В архив обновления корпус не включён; без него эти тесты пропускаются.
Тесты интерфейса и вычисления recall работают без баз данных.

Подробные результаты и ограничения: `SKILL_EXTRACTION.md`, `reports/remarks_summary.json`.
