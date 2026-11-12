# Порядок кандидатов

Активный контракт — `corpus-tfidf-cosine-v3`.
Полное описание: [COSINE_RANKING.md](COSINE_RANKING.md).

Score равен cosine TF-IDF-векторов × 100. Сортировка по полной точности
Score по убыванию, затем по стабильному ID. Неоценённые кандидаты
(score=null, status=insufficient_text) следуют после всех оценённых, по ID.
Округляется только отображение.

`coverage-tfidf-id-v1`, `weighted-coverage-tfidf-v1` и
`weighted-coverage-tfidf-lemma-v2` — исторические версии.
Старые записи сохраняют прежние значения и порядок. Совместимые функции
в ranking.py/weighted_ranking.py не используются для новых HTTP-рейтингов.
