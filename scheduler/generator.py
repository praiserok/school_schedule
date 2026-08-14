"""
Генератор розкладу на OR-Tools CP-SAT.

Змінні: x[a_i, day, period] = 1 якщо завдання a_i в цей слот.

Обмеження:
  1. hours_per_week виконано
  2. Клас — конфлікт з урахуванням груп:
       - ціла-клас × ціла-клас ≤ 1
       - одна група ≤ 1 урок/слот
       - ціла-клас не може збігатись з груповим уроком
  3. Вчитель — 1 урок в слот
  4. Доступність вчителя
  5. max_lessons_per_day для вчителя
  6. Групи одного (клас, предмет) — ставляться ОДНОЧАСНО (co-scheduling)
  7. Рівномірне навантаження класу (по канонічних завданнях)
  8. Без вікон (по канонічних завданнях)

Кімнати:
  - Предметний кабінет → якщо предмет вимагає спец. кабінет
  - Основний кабінет класу → якщо є і немає спец. вимоги
  - Будь-який вільний загальний кабінет
"""
from collections import defaultdict
from math import floor, ceil
from ortools.sat.python import cp_model

from .models import Schedule, Lesson, Teacher, SchoolClass, TeacherSubject, Room


def generate(schedule_id: int) -> tuple[bool, str]:
    schedule = Schedule.objects.get(pk=schedule_id)
    D = schedule.days_per_week
    P = schedule.lessons_per_day

    assignments = list(
        TeacherSubject.objects.select_related(
            'teacher', 'subject', 'school_class', 'school_class__home_room'
        ).all()
    )
    teachers = list(Teacher.objects.all())
    classes  = list(SchoolClass.objects.select_related('home_room').all())
    rooms    = list(Room.objects.select_related('subject').all())

    # Ємність спеціалізованих кабінетів: subject_pk → сумарна кількість одночасних місць
    specialized_capacity: dict[int, int] = defaultdict(int)
    for r in rooms:
        if r.subject_id is not None:
            specialized_capacity[r.subject_id] += r.max_simultaneous

    # Індекси за класом і вчителем
    class_assignments   = {c.pk: [] for c in classes}
    teacher_assignments = {t.pk: [] for t in teachers}
    for a_i, a in enumerate(assignments):
        class_assignments[a.school_class_id].append(a_i)
        teacher_assignments[a.teacher_id].append(a_i)

    # ── Групи ──────────────────────────────────────────────────────────────────
    # group_map[(cls_pk, subj_pk)][group_num] = a_i
    group_map: dict[tuple, dict[int, int]] = defaultdict(dict)
    for a_i, a in enumerate(assignments):
        if a.group is not None:
            group_map[(a.school_class_id, a.subject_id)][a.group] = a_i

    # Канонічні завдання: для балансу/без вікон рахуємо кожен "слот класу" один раз.
    # Групові уроки одного предмету йдуть одночасно → беремо тільки першу групу.
    seen_group_pairs: set[tuple] = set()
    canonical: dict[int, list[int]] = {c.pk: [] for c in classes}
    for a_i, a in enumerate(assignments):
        if a.group is None:
            canonical[a.school_class_id].append(a_i)
        else:
            key = (a.school_class_id, a.subject_id)
            if key not in seen_group_pairs:
                seen_group_pairs.add(key)
                canonical[a.school_class_id].append(a_i)

    # Двотижневий цикл: 3.5 год/тиж → 7 уроків за 2 тижні
    D2 = D * 2
    SLOTS = D2 * P
    slot = lambda d, p: d * P + p  # d in 0..D2-1

    # Кількість уроків за 2 тижні для кожного завдання (ціле число)
    lessons_2w = {a_i: round(float(a.hours_per_week) * 2) for a_i, a in enumerate(assignments)}

    class_total = {
        c.pk: sum(lessons_2w[a_i] for a_i in canonical[c.pk])
        for c in classes
    }

    # ─────────────────────────────────────────────────────────────────────────
    model = cp_model.CpModel()

    x = {}
    for a_i in range(len(assignments)):
        for s in range(SLOTS):
            x[a_i, s] = model.new_bool_var(f'x{a_i}_{s}')

    # 1. Уроків за 2 тижні
    for a_i in range(len(assignments)):
        model.add(sum(x[a_i, s] for s in range(SLOTS)) == lessons_2w[a_i])

    # 2. Класовий конфлікт (з урахуванням груп)
    for c in classes:
        whole = [a_i for a_i in class_assignments[c.pk]
                 if assignments[a_i].group is None]
        grp: dict[int, list[int]] = defaultdict(list)
        for a_i in class_assignments[c.pk]:
            if assignments[a_i].group is not None:
                grp[assignments[a_i].group].append(a_i)

        for s in range(SLOTS):
            # Цілі-класові уроки: не більше одного
            if whole:
                model.add(sum(x[a_i, s] for a_i in whole) <= 1)
            # Кожна група: не більше одного урока
            for g_ais in grp.values():
                model.add(sum(x[a_i, s] for a_i in g_ais) <= 1)
            # Цілий клас та групові уроки не можуть збігатись
            for wc_ai in whole:
                for g_ai in (ai for g_ais in grp.values() for ai in g_ais):
                    model.add(x[wc_ai, s] + x[g_ai, s] <= 1)

    # 3. Вчитель ≤ 1 урок в слот
    for t in teachers:
        st = teacher_assignments[t.pk]
        for s in range(SLOTS):
            model.add(sum(x[a_i, s] for a_i in st) <= 1)

    # 4. Доступність вчителя (однакова в обидва тижні)
    for a_i, a in enumerate(assignments):
        mask = a.teacher.available_days[:D].ljust(D, '0')
        for d in range(D2):
            if mask[d % D] != '1':
                for p in range(P):
                    model.add(x[a_i, slot(d, p)] == 0)

    # 5. max_lessons_per_day для вчителя (кожен з D2 днів окремо)
    for t in teachers:
        st = teacher_assignments[t.pk]
        for d in range(D2):
            model.add(sum(x[a_i, slot(d, p)] for a_i in st for p in range(P))
                      <= t.max_lessons_per_day)

    # 6. Групові уроки одного (клас, предмет):
    #    • різні вчителі → ставимо ОДНОЧАСНО (спліт класу в один і той же слот)
    #    • один вчитель → НЕ примушуємо (він веде групи по черзі в різний час;
    #      обмеження 3 вже не дозволить двох уроків одночасно)
    for (cls_pk, subj_pk), gmap in group_map.items():
        group_ais = list(gmap.values())
        if len(group_ais) >= 2:
            teacher_ids = {assignments[a_i].teacher_id for a_i in group_ais}
            if len(teacher_ids) > 1:
                rep = group_ais[0]
                for other in group_ais[1:]:
                    for s in range(SLOTS):
                        model.add(x[rep, s] == x[other, s])

    # 7. Рівномірне навантаження класу по D2 днях (верхня межа).
    for c in classes:
        sc = canonical[c.pk]
        if not sc or class_total[c.pk] == 0:
            continue
        hi = ceil(class_total[c.pk] / D2)
        for d in range(D2):
            day_sum = sum(x[a_i, slot(d, p)] for a_i in sc for p in range(P))
            model.add(day_sum <= hi)

    # 8. Без вікон — уроки класу йдуть підряд від 1-го (по кожному з D2 днів).
    for c in classes:
        sc = canonical[c.pk]
        if not sc:
            continue
        for d in range(D2):
            for p in range(1, P):
                curr = sum(x[a_i, slot(d, p)]   for a_i in sc)
                prev = sum(x[a_i, slot(d, p-1)] for a_i in sc)
                model.add(curr <= prev)

    # 9. Не ставити один предмет двічі підряд (якщо can_be_double=False)
    for a_i, a in enumerate(assignments):
        if not a.subject.can_be_double:
            for d in range(D2):
                for p in range(P - 1):
                    model.add(x[a_i, slot(d, p)] + x[a_i, slot(d, p + 1)] <= 1)

    # 10. Ємність спеціалізованих кабінетів враховується під час генерації.
    #     Якщо предмет дозволяє спільний кабінет — cap = sum(max_simultaneous).
    #     Якщо ні — cap = кількість спеціалізованих кімнат (1 клас на кімнату).
    #     Це узгоджено з логікою _can_use у розподілі кімнат.
    subj_assignments: dict[int, list[int]] = defaultdict(list)
    for a_i, a in enumerate(assignments):
        if a.subject_id in specialized_capacity:
            subj_assignments[a.subject_id].append(a_i)
    for subj_pk, ais in subj_assignments.items():
        subj_obj = assignments[ais[0]].subject
        if subj_obj.allow_shared_room:
            cap = specialized_capacity[subj_pk]
        else:
            cap = sum(1 for r in rooms if r.subject_id == subj_pk)
        for s in range(SLOTS):
            model.add(sum(x[a_i, s] for a_i in ais) <= cap)

    # 11. Несумісні паралелі зі спільним кабінетом не можуть стояти в один слот.
    #     Замість пар класів (O(n²)) використовуємо індикатори на рівні паралелей:
    #     has_g[grade, s] = 1 ↔ хоч один клас цієї паралелі в слоті s.
    #     Це скорочує кількість обмежень з O(class_pairs × slots)
    #     до O(grade_pairs × slots).
    for subj_pk, ais in subj_assignments.items():
        subj_obj = assignments[ais[0]].subject
        if not subj_obj.allow_shared_room:
            continue

        grade_ais: dict[int, list[int]] = defaultdict(list)
        for a_i in ais:
            grade_ais[assignments[a_i].school_class.grade].append(a_i)
        grades = sorted(grade_ais)

        if len(grades) < 2:
            continue

        # Індикатори: has_g[grade, slot] = 1 ↔ хоч один клас паралелі в цьому слоті
        has_g: dict = {}
        for g in grades:
            g_ais = grade_ais[g]
            for s in range(SLOTS):
                v = model.new_bool_var(f'hg_{subj_pk}_{g}_{s}')
                has_g[g, s] = v
                model.add(v <= sum(x[a_i, s] for a_i in g_ais))
                for a_i in g_ais:
                    model.add(v >= x[a_i, s])

        # Несумісні пари паралелей — не можуть бути в одному слоті
        for i, g1 in enumerate(grades):
            for g2 in grades[i + 1:]:
                if abs(g1 - g2) > subj_obj.max_grade_diff:
                    for s in range(SLOTS):
                        model.add(has_g[g1, s] + has_g[g2, s] <= 1)

    # ─────────────────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 300.0
    solver.parameters.num_search_workers = 8
    solver.parameters.log_search_progress = False

    status = solver.solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return False, f'Розвʼязок не знайдено ({solver.status_name(status)})'

    # ── Кімнати ───────────────────────────────────────────────────────────────
    # slot → room_pk → [grades of classes currently using this room]
    room_usage: dict[int, dict[int, list[int]]] = {}
    # home_room IDs — не видавати стороннім класам у кроці 3
    home_room_ids = {c.home_room_id for c in classes if c.home_room_id}

    def _can_use(r, subject, grade, s):
        """Чи може клас з даною паралеллю використати кімнату в слоті s."""
        grades_here = list(dict.fromkeys(room_usage.get(s, {}).get(r.pk, [])))  # унікальні, зі збереженням порядку
        if len(grades_here) >= r.max_simultaneous:
            return False
        if grades_here:
            # Кімната вже зайнята — дозволяємо лише якщо предмет підтримує спільний кабінет
            # і різниця паралелей не перевищує ліміт
            if not subject.allow_shared_room:
                return False
            if any(abs(grade - g) > subject.max_grade_diff for g in grades_here):
                return False
        return True

    def _mark_used(r, grade, s):
        room_usage.setdefault(s, {}).setdefault(r.pk, []).append(grade)

    def find_room(subject, school_class, s):
        grade = school_class.grade
        # 1. Кабінет спеціально для цього предмету
        specialized = [r for r in rooms if r.subject_id == subject.pk]
        for r in specialized:
            if _can_use(r, subject, grade, s):
                return r
        # Якщо для предмету є спеціалізовані кабінети — інші не підходять
        if specialized:
            return None
        # 2. Основний кабінет класу (тільки якщо він вільний повністю)
        if school_class.home_room_id:
            hr = school_class.home_room
            if hr.subject_id is None and _can_use(hr, subject, grade, s):
                return hr
        # 3. Будь-який вільний загальний кабінет (не чужий home_room)
        for r in rooms:
            if r.subject_id is None and r.pk not in home_room_ids and _can_use(r, subject, grade, s):
                return r
        return None

    # Збираємо всі заплановані уроки і сортуємо:
    # • по слоту (день×урок)
    # • по паралелі всередині слоту для предметів зі спільним кабінетом —
    #   клас grade=1 йде першим, grade=2 другим тощо.
    #   Це гарантує, що grade-сумісні класи отримують кабінет разом.
    shared_subj_ids = {
        pk for pk, ais in subj_assignments.items()
        if assignments[ais[0]].subject.allow_shared_room
    }

    scheduled = []
    for a_i, a in enumerate(assignments):
        for d in range(D2):
            for p in range(P):
                s = slot(d, p)
                if solver.value(x[a_i, s]):
                    grade_key = a.school_class.grade if a.subject_id in shared_subj_ids else 0
                    scheduled.append((s, grade_key, a_i, d, p))

    scheduled.sort(key=lambda t: (t[0], t[1]))

    Lesson.objects.filter(schedule=schedule).delete()
    to_create = []
    for s, _, a_i, d, p in scheduled:
        a = assignments[a_i]
        room = find_room(a.subject, a.school_class, s)
        if room:
            _mark_used(room, a.school_class.grade, s)
        to_create.append(Lesson(
            schedule=schedule,
            school_class=a.school_class,
            subject=a.subject,
            teacher=a.teacher,
            room=room,
            day=d % D,
            period=p,
            week=d // D,
        ))

    Lesson.objects.bulk_create(to_create)
    return True, f'Готово! {len(to_create)} уроків за {solver.wall_time:.1f}с'
