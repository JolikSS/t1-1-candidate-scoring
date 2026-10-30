"""Versioned aliases and activity verbs, with no external dependencies."""

EXPRESSION_RULES_VERSION = "expressions-1.1"
CATALOG_VERSION = "professional-catalog-1.1"

# One source of defaults for TF-IDF and the integrated Parser.
DEFAULT_SKILLS = (
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", ".NET", "SQL", "PostgreSQL", "MySQL",
    "MongoDB", "Neo4j", "Linux", "Windows Server", "Docker", "Kubernetes", "Git", "FastAPI", "Django",
    "Flask", "React", "Angular", "Vue", "Node.js", "REST API", "GraphQL", "Redis", "Kafka", "RabbitMQ",
    "AWS", "Azure", "GCP", "Terraform", "Ansible", "Jenkins", "GitLab CI", "CI/CD", "HTML", "CSS",
    "PowerShell", "Bash", "VMware", "VMware ESXi", "MS Exchange", "Active Directory", "TCP/IP", "DNS",
    "Pandas", "NumPy", "scikit-learn", "TensorFlow", "PyTorch", "Excel", "Power BI", "Tableau",
    "Agile", "Scrum", "1C", "SAP", "English", "Ukrainian", "Russian",
    "Hadoop", "Spark", "Flink", "Doris", "Kudu", "Iceberg", "Airflow", "DWH", "Data Lake",
    "ETL", "ELT", "Kotlin", "NiFi", "ClickHouse", "Helm", "Prometheus", "Grafana", "IaC",
)

SKILL_ALIASES = {
    "python": ("python 2", "python 3", "python2", "python3"),
    "javascript": ("js", "java script"),
    "postgresql": ("postgres",),
    "ms exchange": ("microsoft exchange",),
    "sql": ("ms sql", "microsoft sql", "sql server", "ms sql server", "microsoft sql server"),
    "ms isa": ("microsoft isa",),
    "fastapi": ("fast api",),
    "rest api": ("rest apis", "restful api", "restful apis"),
    "c++": ("cpp", "c plus plus"),
    "c#": ("c sharp", "csharp", "c-sharp"),
    "node.js": ("nodejs", "node js"),
    ".net": ("dotnet", "dot net"),
    "ci/cd": ("ci cd", "ci-cd"),
    "kubernetes": ("k8s",),
    "active directory": ("active-directory",),
    "windows server": ("windows-server",),
    "scikit-learn": ("scikit learn", "sklearn"),
    "hadoop": ("apache hadoop",),
    "spark": ("apache spark",),
    "flink": ("apache flink",),
    "doris": ("apache doris",),
    "kudu": ("apache kudu",),
    "iceberg": ("apache iceberg",),
    "airflow": ("apache airflow",),
    "nifi": ("apache nifi",),
    "dwh": ("data warehouse", "data warehousing"),
    "data lake": ("data-lake",),
    "iac": ("infrastructure as code", "infrastructure-as-code"),
}

DISPLAY_NAMES = {
    "python": "Python", "javascript": "JavaScript", "postgresql": "PostgreSQL",
    "ms exchange": "MS Exchange", "sql": "SQL", "ms isa": "MS ISA", "fastapi": "FastAPI",
    "rest api": "REST API", "c++": "C++", "c#": "C#", "node.js": "Node.js", ".net": ".NET",
    "ci/cd": "CI/CD", "kubernetes": "Kubernetes", "active directory": "Active Directory",
    "windows server": "Windows Server", "scikit-learn": "scikit-learn",
    "hadoop": "Hadoop", "spark": "Spark", "flink": "Flink", "doris": "Doris",
    "kudu": "Kudu", "iceberg": "Iceberg", "airflow": "Airflow", "nifi": "NiFi",
    "dwh": "DWH", "data lake": "Data Lake", "iac": "IaC",
}

# Each verb must immediately precede a known target (optionally through one
# listed preposition). Arbitrary intervening words are not consumed.
ACTIVITY_VERBS = {
    "develop": r"разработ(?:ка|ки|ку|кой)|разрабатыва(?:л[аи]?|ю|ем)|создава(?:л[аи]?|ю)|develop(?:ed|ing|ment)?|build(?:ing)?|built",
    "administer": r"администрировани[ея]|администрирова(?:л[аи]?|ть)|администрирую|administer(?:ed|ing)?|administration",
    "deploy": r"разв[её]ртывани[ея]|разворачивал[аи]?|развернул[аи]?|deploy(?:ed|ing|ment)?",
    "configure": r"настройк[аиу]|настраива(?:л[аи]?|ю)|configur(?:e|ed|ing|ation)",
    "test": r"тестировани[ея]|тестирова(?:л[аи]?|ть)|тестирую|test(?:ed|ing)?",
}
