"""Explicitly synthetic technical fixtures for 20 professional directions."""

CASES = (
    ("backend", "Backend developer", ("Python", "FastAPI")),
    ("frontend", "Frontend developer", ("TypeScript", "React")),
    ("data", "Data engineer", ("Spark", "Airflow")),
    ("devops", "DevOps engineer", ("Terraform", "Kubernetes")),
    ("qa", "QA engineer", ("Selenium", "test automation")),
    ("security", "Security analyst", ("penetration testing", "SIEM")),
    ("accounting", "Accountant", ("IFRS", "financial reporting")),
    ("sales", "Sales manager", ("CRM", "sales forecasting")),
    ("marketing", "Marketing specialist", ("SEO", "contextual advertising")),
    ("hr", "Recruiter", ("candidate sourcing", "interviewing")),
    ("logistics", "Logistics coordinator", ("route planning", "warehouse management")),
    ("procurement", "Procurement specialist", ("tender documentation", "supplier evaluation")),
    ("legal", "Legal counsel", ("contract law", "legal drafting")),
    ("design", "Graphic designer", ("Figma", "Adobe Illustrator")),
    ("construction", "Construction engineer", ("AutoCAD", "construction supervision")),
    ("healthcare", "Nurse", ("patient care", "medical documentation")),
    ("education", "Teacher", ("lesson planning", "student assessment")),
    ("manufacturing", "Production engineer", ("Lean manufacturing", "quality control")),
    ("hospitality", "Hotel administrator", ("guest relations", "hotel reservations")),
    ("agriculture", "Agronomist", ("crop rotation", "soil analysis")),
)


def technical_dataset():
    vacancies, resumes, judgments = [], [], []
    for i, (domain, title, skills) in enumerate(CASES):
        vacancies.append(
            {
                "id": f"v{i}",
                "title": title,
                "domain": domain,
                "required_skills": list(skills),
                "critical_skills": [skills[0]],
                "description": ", ".join(skills),
            }
        )
        for j in range(3):
            resumes.append(
                {
                    "resume_id": f"r{i}-{j}",
                    "title": title,
                    "skills": list(skills),
                    "anonymized_text": f"Experience\n{', '.join(skills)}\nTechnical fixture {i} {j}",
                }
            )
    for i in range(len(CASES)):
        split = "validation" if i < 10 else "test"
        other = (i + 1) % 10 + (0 if i < 10 else 10)
        for j in range(3):
            judgments.extend(
                [
                    {"vacancy_id": f"v{i}", "resume_id": f"r{i}-{j}", "label": 1, "split": split},
                    {"vacancy_id": f"v{i}", "resume_id": f"r{other}-{j}", "label": 0, "split": split},
                ]
            )
    return {
        "schema_version": "candidate-benchmark-v1",
        "source": "synthetic",
        "vacancies": vacancies,
        "resumes": resumes,
        "judgments": judgments,
    }
