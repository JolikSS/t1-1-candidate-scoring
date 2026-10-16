# Сценарий демонстрации

Используйте файл `tests/fixtures/hh_public_resume_redacted.txt`. Это обезличенная
выжимка из публичного [образца экспорта HH](https://hh.ru/article/14285):
удалены идентификаторы, работодатели, даты, рекомендации и комментарии; оставлены
опыт и технологические навыки. HH поддерживает выгрузку в [PDF, RTF и TXT](https://feedback.hh.ru/knowledge-base/article/6476).

## Подготовка

1. Создайте `.env` из `.env.example`, задайте локальный пароль и запустите
   Neo4j командой `docker compose up -d neo4j`.
2. В окне PowerShell для API задайте `NEO4J_URI`, `NEO4J_USERNAME` и
   `NEO4J_PASSWORD`, затем запустите `python -m uvicorn api.main:app --reload`.
3. В другом окне запустите `python -m streamlit run ui/app.py`.
4. Убедитесь, что `http://127.0.0.1:8000/health` отвечает `{"status":"ok", ...}`.

## Показать пакетный скоринг

1. Запустите API и UI по инструкции в `README.md`.
2. Оставьте выбранные по умолчанию вакансию и навыки. Описание вакансии
   заполнять не нужно для расчёта покрытия навыков.
3. Нажмите «Рассчитать рейтинг». UI автоматически отправит
   `data/workua_sample_70.zip`; загружать файлы вручную не требуется.
4. В результатах должно быть до 70 кандидатов. Score — доля найденных
   обязательных навыков. TF-IDF будет пустым, пока не добавлено описание вакансии.
5. Для отдельной проверки парсера используйте обезличенный HH-образец
   `tests/fixtures/hh_public_resume_redacted.txt`.
6. Покажите `/docs`, затем откройте Neo4j Browser на `http://localhost:7474`
   и выполните запрос:

   ```cypher
   MATCH (candidate:Candidate)-[:HAS_RESUME]->(resume:Resume)
         -[evaluation:SCORED_FOR]->(vacancy:Vacancy)
   RETURN candidate, resume, evaluation, vacancy
   ORDER BY evaluation.scored_at DESC
   LIMIT 10
   ```

Подчеркните, что 60% означает покрытие перечисленных навыков; это не
автоматическое решение о найме и не оценка качества кандидата в целом.
