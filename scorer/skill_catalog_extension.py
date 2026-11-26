"""Reviewed general competencies; no entries derived from V01–V05 gold files."""

COMPETENCIES = {
    "fundamentals": {
        "ООП": ("oop", "объектно-ориентированное программирование", "объектно ориентированное программирование",
                "объектно-ориентированного программирования"),
        "Алгоритмы": ("алгоритмы", "алгоритмов", "algorithms"),
        "Структуры данных": ("структуры данных", "структур данных", "структурами данных", "data structures"),
        "Многопоточность": ("многопоточное программирование", "многопоточного программирования",
                           "многопоточность", "многопоточности", "multithreading", "multithreaded programming"),
        "Асинхронное программирование": ("асинхронного программирования", "asynchronous programming"),
        "Шаблоны проектирования": ("шаблонов проектирования", "паттерны проектирования",
                                  "паттернов проектирования", "design patterns"),
        "SOLID": (), "TDD": ("test driven development", "test-driven development"),
        "DDD": ("domain driven design", "domain-driven design"),
        "Микросервисы": ("микросервисная архитектура", "микросервисной архитектуры", "микросервисов",
                         "microservices", "microservice architecture"),
        "Проектирование баз данных": ("проектирования баз данных", "database design"),
        "Оптимизация SQL": ("оптимизация sql-запросов", "оптимизации sql-запросов", "sql optimization"),
        "Распределённые системы": ("распределенные системы", "распределенных систем", "distributed systems"),
    },
    "testing": {
        "Unit testing": ("unit-тесты", "unit-тестов", "unit tests", "unit testing", "юнит-тесты",
                         "модульные тесты", "модульных тестов", "модульное тестирование"),
        "Integration testing": ("integration tests", "интеграционные тесты", "интеграционных тестов",
                                "интеграционное тестирование", "интеграционного тестирования"),
        "E2E testing": ("end-to-end testing", "e2e-тесты", "e2e-тестов", "сквозное тестирование"),
        "Нагрузочное тестирование": ("нагрузочного тестирования", "load testing"),
        "Автоматизация тестирования": ("автоматизации тестирования", "test automation"),
        "Code review": ("code-review", "код-ревью", "ревью кода", "проверка кода"),
        "JUnit": (), "TestNG": (), "Selenium": (), "Cypress": (), "Mocha": (), "NUnit": (),
    },
    "backend_and_data": {
        "Spring": ("spring framework",), "Spring Boot": ("springboot",), "Hibernate": (),
        "ASP.NET Core": ("asp net core",), "Entity Framework": ("entity framework core",),
        "NestJS": ("nest.js", "nest js"), "Express": ("express.js", "expressjs"),
        "Next.js": ("nextjs", "next js"), "Svelte": (), "RxJS": (), "VBA": (),
        "Статистический анализ": ("статистического анализа", "statistical analysis"),
        "Машинное обучение": ("машинного обучения", "machine learning"),
        "Обработка естественного языка": ("обработки естественного языка", "natural language processing", "nlp"),
        "BPMN": (), "UML": (), "OAuth 2.0": ("oauth2", "oauth 2"), "JWT": (),
    },
    "collaboration": {
        "Командная работа": ("работа в команде", "работы в команде", "работать в команде", "teamwork"),
        "Коммуникация": ("коммуникативные навыки", "коммуникационные навыки", "communication skills"),
        "Критическое мышление": ("критического мышления", "critical thinking"),
        "Тайм-менеджмент": ("управление временем", "time management"),
        "Решение проблем": ("решения проблем", "problem solving", "problem-solving"),
        "Kanban": (),
    },
}

COMPETENCIES.update({
    "web_and_security": {
        "HTTP": ("https",), "TCP": (), "UDP": (), "WebSocket": ("websockets", "web socket"),
        "React Hooks": ("react hooks", "хуки react", "react с hooks"),
        "MobX": (), "Effector": (), "Koa": ("koa.js",), "Playwright": (),
        "React Testing Library": ("react test library", "react testing library"),
        "Centrifugo": (), "DOM": (), "Async/await": ("async / await",),
        "Promises": ("промисы", "промисов", "promises", "promise"),
        "Адаптивная вёрстка": ("адаптивная верстка", "адаптивной верстки", "responsive design"),
        "Вёрстка": ("верстка", "верстки", "верстку", "layout development"),
        "Кеширование": ("кэширование", "кеширования", "кэширования", "caching"),
        "ORM": ("object relational mapping", "object-relational mapping"),
        "Реляционные базы данных": ("реляционных баз данных", "реляционными базами данных", "relational databases"),
        "Индексы БД": ("индексы", "индексов", "database indexes"),
        "Транзакции": ("транзакций", "транзакциями", "transactions"),
        "Key-value databases": ("key-value", "key value", "ключ-значение"),
        "In-memory databases": ("in-memory", "in memory databases"),
        "XSS": (), "CSRF": (), "SQL injection": ("sql-инъекции", "sql injections", "sql-инъекций"),
        "Rate limiting": ("rate-limiting", "ограничение частоты запросов"),
        "Валидация данных": ("валидация", "валидации", "data validation"),
        "Контроль доступа": ("права доступа", "прав доступа", "управление доступом", "access control"),
        "Информационная безопасность": ("информационной безопасности", "обеспечение безопасности", "information security"),
        "OWASP": (), "TLS": (), "SSL": (), "SAML": (), "OpenID Connect": ("oidc",),
    },
    "infrastructure": {
        "VLAN": (), "VPN": (), "802.1X": (), "MCSA": (), "MCSE": (), "LPIC": ("lpic-1", "lpic-2", "lpic-3"),
        "Kaspersky Endpoint Security": ("kaspersky endpoint security для бизнеса",),
        "Samba": ("sambadc", "samba dc"), "FreeIPA": (), "Astra Linux": (), "ALT Linux": (),
        "Kerberos": (), "Yandex Cloud": ("yandex.cloud", "яндекс облако", "яндекс облака"),
        "VictoriaMetrics": ("victoria metrics",), "Azure DevOps": (), "D-Link": ("d-link", "d link"),
        "HP": ("hewlett packard",), "ITSM": (), "InfraManager": ("инфраменеджер",),
        "Сетевые технологии": ("сетевых технологий", "компьютерные сети", "компьютерных сетей", "networking"),
        "Администрирование систем": ("системное администрирование", "системного администрирования", "system administration"),
        "Контейнеризация": ("контейнеризации", "containerization"),
        "Оркестрация контейнеров": ("оркестрации контейнеров", "container orchestration"),
        "Мониторинг": ("мониторинга", "monitoring"), "Логирование": ("логирования", "logging"),
        "Трассировка": ("трассировки", "tracing"),
        "Высокая доступность": ("высокодоступной", "высокодоступных", "high availability"),
        "Автоматизация": ("автоматизировать", "автоматизации", "automation"),
        "Техническая поддержка": ("поддержка пользователей", "поддержки пользователей", "technical support"),
        "Управление инцидентами": ("разбор инцидентов", "разбора инцидентов", "incident management"),
    },
    "data_and_analysis": {
        "PySpark": (), "YARN": (), "Soda DQ": ("soda data quality",),
        "Data Quality": ("качество данных", "качества данных"), "SCD": ("slowly changing dimensions",),
        "Моделирование данных": ("моделирования данных", "data modeling", "data modelling"),
        "Витрины данных": ("витрин данных", "витринами данных", "data marts", "data mart"),
        "Data pipelines": ("пайплайны обработки данных", "пайплайнов обработки данных", "data pipeline"),
        "Airflow DAG": ("dag", "dags"), "Backfill": ("backfill-стратегии",),
        "Бизнес-анализ": ("бизнес анализ", "бизнес-требований", "бизнес-требования", "business analysis"),
        "Техническая документация": ("технической документации", "разработка документации", "ведения документации",
                                     "ведение документации", "technical documentation"),
        "ETL pipelines": ("etl-пайплайны", "etl-пайплайнов"),
        "Рефакторинг": ("рефакторинга", "refactoring"),
        "E-commerce": ("ecommerce", "e commerce", "электронная коммерция"),
        "Логистика": ("логистики", "logistics"), "SLA": (),
        "Процессы и потоки": ("процессов и потоков", "процессов / потоков", "processes and threads"),
        "Файловые системы": ("файловой системы", "файловых систем", "file systems"),
        "Фоновые задачи": ("фоновых задач", "background tasks"),
        "Управление релизами": ("выпуск релизов", "выпуска релизов", "release management"),
        "Интеграция систем": ("интеграции с внешними", "интеграция с внешними", "system integration"),
    },
    "general_soft_skills": {
        "Ответственность": ("ответственности", "responsibility"),
        "Самостоятельность": ("самостоятельно", "самостоятельности", "independence"),
        "Обучаемость": ("готовность учиться", "быстро обучаться", "learning ability"),
        "Инициативность": ("инициативности", "initiative"),
        "Внимание к деталям": ("внимательность", "attention to detail"),
        "Аналитическое мышление": ("аналитического мышления", "analytical thinking"),
        "Управление командой": ("распределение задач", "контроль задач сотрудников", "team leadership"),
        "Коммуникация с бизнесом": ("взаимодействие с бизнесом", "коммуникация с бизнес-заказчиками",
                                  "общение с заказчиками", "stakeholder communication"),
        "Презентация результатов": ("презентации результатов", "presentation skills"),
        "Работа с обратной связью": ("обратную связь", "feedback"),
    },
})

COMPETENCIES["programming_concepts"] = {
    "Типы данных": ("типов данных", "data types"), "Функции": ("функций", "functions"),
    "Прототипы": ("прототипов", "prototypes"), "Наследование": ("наследования", "inheritance"),
    "Классы": ("классов", "classes"), "Обработка ошибок": ("обработки ошибок", "error handling"),
    "События браузера": ("событий браузера", "browser events"),
    "Сетевые запросы": ("сетевых запросов", "network requests"),
    "Мемоизация": ("мемоизации", "memoization"),
    "Архитектурные паттерны": ("архитектурных паттернов", "architectural patterns"),
    "Жизненный цикл разработки ПО": ("от анализа до выхода в промышленную эксплуатацию", "software development lifecycle", "sdlc"),
    "Оптимизация производительности": ("оптимизации производительности", "performance optimization"),
    "Управление состоянием": ("клиентского состояния", "state management"),
    "Backend development": ("backend-части", "backend-разработка", "backend development"),
    "API integration": ("взаимодействия с api", "взаимодействие с api", "api integrations"),
    "API testing": ("тестирование api", "тестирования api", "api tests"),
    "Преобразование данных": ("преобразования данных", "data transformation"),
    "Сетевое оборудование": ("сетевого оборудования", "сетевой инфраструктурой", "network equipment"),
    "L3": (), "Grain": (), "Airflow operators": ("airflow операторы",),
    "Airflow sensors": ("airflow сенсоры",), "Last-mile": ("last mile",), "Cross-border": ("cross border",),
    "Метрики": ("метрик", "metrics"),
    "Импортозамещение": ("отечественные программно-аппаратные решения", "импортозамещения"),
    "Информационные технологии": ("информационных технологий", "information technology"),
    "Организация рабочих мест": ("организации рабочих мест",),
}

EXTRA_SKILLS = tuple(name for entries in COMPETENCIES.values() for name in entries)
EXTRA_ALIASES = {name.casefold().replace("ё", "е"): aliases
                 for entries in COMPETENCIES.values() for name, aliases in entries.items()}
EXTRA_DISPLAY_NAMES = {name.casefold().replace("ё", "е"): name for name in EXTRA_SKILLS}

# Additional inflections/spellings reviewed against development documents.
# They do not imply related technologies or convert company context to requirements.
EXTRA_ALIASES["react hooks"] += ("react with hooks", "глубокое понимание хуков")
EXTRA_ALIASES["code review"] += ("код ревью", "код ревью коллег",)
EXTRA_ALIASES["командная работа"] += ("командной работы", "совместная работа с разработчиками")
EXTRA_ALIASES["e2e testing"] += ("e2e тестов", "e2e тесты")
EXTRA_ALIASES["коммуникация с бизнесом"] += ("работа с разработчиками и бизнесом",)
EXTRA_ALIASES["реляционные базы данных"] += ("реляционных бд", "реляционными бд")
EXTRA_ALIASES["in-memory databases"] += ("inmemory бд", "inmemory")
EXTRA_ALIASES["сетевые технологии"] += ("сетевого взаимодействия",)
EXTRA_ALIASES["администрирование систем"] += ("администрирование серверов", "администрирования linux",)
EXTRA_ALIASES["astra linux"] += ("astra",)
EXTRA_ALIASES["моделирование данных"] += ("моделирование dwh", "моделирования dwh")
EXTRA_ALIASES["техническая документация"] += ("подготовка технических спецификаций", "подготовки технических спецификаций")
EXTRA_ALIASES["soda dq"] += ("soda",)
EXTRA_ALIASES["нагрузочное тестирование"] += ("нагрузочных тестов", "нагрузочные тесты")
EXTRA_ALIASES["управление инцидентами"] += ("разбирать инциденты", "разбор инцидентов с данными")
EXTRA_ALIASES["автоматизация тестирования"] += ("автотесты", "автотестов")
EXTRA_ALIASES["файловые системы"] += ("устройства фс", "фс")
