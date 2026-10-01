import json
import time



# Пока нет TF-IDF, используем просто поиск вхождений
def calculate_score(resume_skills, vacancy_skills):
    if not vacancy_skills:
        return 0.0
    # Считаем пересечение навыков
    match_count = len(set(resume_skills).intersection(set(vacancy_skills)))
    return (match_count / len(vacancy_skills)) * 100


# -------------------------

def run_benchmark():
    # 1. Загружаем данные
    with open("data/vacancies.json", "r", encoding="utf-8") as f:
        vacancies = json.load(f)
    with open("data/resumes.json", "r", encoding="utf-8") as f:
        resumes = json.load(f)

    print(f"Запуск бенчмарка: {len(vacancies)} вакансий, {len(resumes)} резюме.")
    print("-" * 40)

    total_start_time = time.time()
    max_time_per_resume = 0
    sanity_check_failed = False

    # 2. Прогоняем каждое резюме через каждую вакансию
    for vacancy in vacancies:
        print(f"\nВакансия: {vacancy['title']}")
        results = []

        for resume in resumes:
            resume_start = time.time()

            # ВЫЗОВ АЛГОРИТМА
            score = calculate_score(resume['skills'], vacancy['required_skills'])

            resume_time = time.time() - resume_start
            if resume_time > max_time_per_resume:
                max_time_per_resume = resume_time

            # Проверка здравого смысла: если навыков вообще общих нет, но скор 100%
            has_common_skills = len(set(resume['skills']).intersection(set(vacancy['required_skills']))) > 0
            if not has_common_skills and score == 100.0:
                sanity_check_failed = True

            results.append({"name": resume['name'], "score": score})

        # Сортируем кандидатов по убыванию
        results.sort(key=lambda x: x['score'], reverse=True)

        print("Топ-3 кандидата:")
        for res in results[:3]:
            print(f" - {res['name']}: {res['score']:.1f}%")

    # 3. Вывод метрик
    total_time = time.time() - total_start_time
    print("\n" + "=" * 40)
    print("ИТОГИ БЕНЧМАРКА:")
    print(f"Общее время обработки пачки: {total_time:.4f} сек (Норматив: до 30 мин)")
    print(f"Макс. время на одно резюме: {max_time_per_resume:.6f} сек (Норматив: до 2 мин)")

    if sanity_check_failed:
        print("ВНИМАНИЕ (Sanity Check): ПРОВАЛЕНО! Алгоритм выдал 100% совпадение совершенно разным текстам.")
    else:
        print("Проверка на адекватность (Sanity Check): ПРОЙДЕНА.")


if __name__ == "__main__":
    run_benchmark()