import json
import random

# Заглушки для генерации, потом расширим, просто ничего в проекте не сделано чтобы сразу ставить внешние библиотеки типа Faker на старте
NAMES = ["Иван Иванов", "Петр Петров", "Анна Смирнова", "Елена Соколова", "Дмитрий Волков"]
SKILLS_POOL = ["Python", "SQL", "Docker", "Git", "C++", "HTML", "CSS", "JavaScript", "Linux", "REST API", "PostgreSQL", "FastAPI"]

vacancies = [
    {"id": "v1", "title": "Python Backend Developer", "required_skills": ["Python", "SQL", "PostgreSQL", "Docker", "REST API"]},
    {"id": "v2", "title": "Frontend Developer", "required_skills": ["HTML", "CSS", "JavaScript", "Git"]},
    {"id": "v3", "title": "C++ Engineer", "required_skills": ["C++", "Linux", "Git", "SQL"]}
]

resumes = []
for i in range(1, 61):  # Генерируем 60 резюме
    # Берем случайные навыки (от 2 до 6 штук)
    candidate_skills = random.sample(SKILLS_POOL, k=random.randint(2, 6))
    resumes.append({
        "id": f"r{i}",
        "name": random.choice(NAMES),
        "skills": candidate_skills,
        "experience": f"Работал с технологиями: {', '.join(candidate_skills)}."
    })

# Сохраняем в папку data
with open("data/vacancies.json", "w", encoding="utf-8") as f:
    json.dump(vacancies, f, ensure_ascii=False, indent=4)

with open("data/resumes.json", "w", encoding="utf-8") as f:
    json.dump(resumes, f, ensure_ascii=False, indent=4)

print("Синтетические данные успешно сгенерированы в папке data/")