from collections import defaultdict

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.db import models
from django.forms import inlineformset_factory

from .models import Teacher, Subject, Room, SchoolClass, TeacherSubject, Schedule, Lesson, BellSchedule, BellPeriod
from .forms import (
    TeacherForm, SubjectForm, RoomForm, SchoolClassForm,
    TeacherSubjectForm, TeacherLoadRowForm, ClassLoadRowForm, ScheduleForm,
    BellScheduleForm, BellPeriodForm, DAYS_LABELS, DAYS_FULL, MAX_PERIODS,
)


# ─── Dashboard ───────────────────────────────────────────────────────────────

def dashboard(request):
    active_schedule = Schedule.objects.filter(is_active=True).first()

    from itertools import groupby
    teacher_qs = list(
        Teacher.objects.prefetch_related(
            models.Prefetch(
                'teachersubject_set',
                queryset=TeacherSubject.objects.select_related('subject').order_by('subject__name'),
            )
        ).order_by('last_name')
    )
    for t in teacher_qs:
        t.total_hours = sum(ts.hours_per_week for ts in t.teachersubject_set.all())

    def first_subject(t):
        ts = t.teachersubject_set.all()
        return ts[0].subject if ts else None

    teacher_qs.sort(key=lambda t: (
        first_subject(t).name if first_subject(t) else '\xff',
        t.last_name,
    ))

    teacher_groups = [
        (subj, list(items))
        for subj, items in groupby(teacher_qs, key=first_subject)
    ]

    return render(request, 'scheduler/dashboard.html', {
        'counts': {
            'teachers': Teacher.objects.count(),
            'subjects': Subject.objects.count(),
            'rooms': Room.objects.count(),
            'classes': SchoolClass.objects.count(),
            'assignments': TeacherSubject.objects.count(),
        },
        'active_schedule': active_schedule,
        'lesson_count': active_schedule.lessons.count() if active_schedule else 0,
        'teacher_groups': teacher_groups,
    })


# ─── Help ─────────────────────────────────────────────────────────────────────

def help_page(request):
    return render(request, 'scheduler/help.html')


# ─── Новий навчальний рік ─────────────────────────────────────────────────────

def new_year(request):
    """
    GET  → прев'ю змін (що буде видалено / зсунуто)
    POST action=reset  → повне очищення бази (залишає Teachers/Subjects/Rooms)
    POST action=shift  → зсув класів на 1 рік + нові 1-ші класи
    POST action=wipe   → видалити абсолютно все (тестові дані)
    """
    from .models import Lesson, Schedule, TeacherSubject, SchoolClass

    classes_by_grade = {}
    for cls in SchoolClass.objects.order_by('grade', 'letter'):
        classes_by_grade.setdefault(cls.grade, []).append(cls)

    max_grade = max(classes_by_grade.keys(), default=0)

    stats = {
        'lessons':     Lesson.objects.count(),
        'schedules':   Schedule.objects.count(),
        'assignments': TeacherSubject.objects.count(),
        'classes':     SchoolClass.objects.count(),
        'teachers':    Teacher.objects.count(),
        'subjects':    Subject.objects.count(),
        'rooms':       Room.objects.count(),
    }

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'wipe':
            # Видалити абсолютно всі дані
            from .models import BellSchedule
            Lesson.objects.all().delete()
            Schedule.objects.all().delete()
            TeacherSubject.objects.all().delete()
            SchoolClass.objects.all().delete()
            Teacher.objects.all().delete()
            Subject.objects.all().delete()
            Room.objects.all().delete()
            BellSchedule.objects.all().delete()
            messages.success(request, 'Базу повністю очищено.')
            return redirect('scheduler:new_year')

        if action == 'reset':
            # Очистити розклади й навантаження, залишити Teachers/Subjects/Rooms/Classes
            Lesson.objects.all().delete()
            Schedule.objects.all().delete()
            TeacherSubject.objects.all().delete()
            messages.success(request, 'Розклади та навантаження очищено.')
            return redirect('scheduler:new_year')

        if action == 'shift':
            # Зсунути паралелі на 1 рік
            new_letters = [
                l.strip().upper()
                for l in request.POST.get('new_grade1_letters', '').split(',')
                if l.strip()
            ]
            # Спочатку видаляємо випускний клас
            SchoolClass.objects.filter(grade=max_grade).delete()
            # Зсуваємо від старшого до молодшого, щоб не порушити unique_together
            for cls in SchoolClass.objects.order_by('-grade'):
                cls.grade += 1
                cls.save()
            # Очищаємо навантаження і розклади (вони більше не відповідають класам)
            Lesson.objects.all().delete()
            Schedule.objects.all().delete()
            TeacherSubject.objects.all().delete()
            # Додаємо нові 1-ші класи
            for letter in new_letters:
                SchoolClass.objects.get_or_create(grade=1, letter=letter)
            messages.success(
                request,
                f'Класи зсунуто. Видалено {max_grade}-ту паралель. '
                f'Додано нових 1-х класів: {len(new_letters)}.'
            )
            return redirect('scheduler:new_year')

    return render(request, 'scheduler/new_year.html', {
        'stats': stats,
        'classes_by_grade': dict(sorted(classes_by_grade.items())),
        'max_grade': max_grade,
    })


# ─── Teachers ────────────────────────────────────────────────────────────────

def teacher_list(request):
    subject_filter = request.GET.get('subject', '')
    search = request.GET.get('q', '').strip()
    qs = Teacher.objects.prefetch_related(
        models.Prefetch(
            'teachersubject_set',
            queryset=TeacherSubject.objects.select_related('subject').order_by('subject__name'),
        )
    )
    if subject_filter:
        qs = qs.filter(teachersubject__subject_id=subject_filter).distinct()

    from itertools import groupby
    teacher_list_data = list(qs.order_by('last_name'))
    if search:
        q = search.lower()
        teacher_list_data = [
            t for t in teacher_list_data
            if q in t.last_name.lower() or q in t.first_name.lower()
        ]

    for t in teacher_list_data:
        t.total_hours = sum(ts.hours_per_week for ts in t.teachersubject_set.all())

    def first_subject(t):
        ts = t.teachersubject_set.all()
        return ts[0].subject if ts else None

    teacher_list_data.sort(key=lambda t: (
        first_subject(t).name if first_subject(t) else '\xff',
        t.last_name,
    ))

    groups = []
    for subj, items in groupby(teacher_list_data, key=first_subject):
        teachers = list(items)
        if subj:
            subj_hours = sum(
                ts.hours_per_week
                for t in teachers
                for ts in t.teachersubject_set.all()
                if ts.subject_id == subj.pk
            )
        else:
            subj_hours = 0
        groups.append((subj, teachers, subj_hours))

    subjects = Subject.objects.order_by('name')
    active_schedule = Schedule.objects.filter(is_active=True).first()
    return render(request, 'scheduler/teacher_list.html', {
        'groups': groups,
        'subjects': subjects,
        'subject_filter': subject_filter,
        'search': search,
        'total_count': len(teacher_list_data),
        'active_schedule': active_schedule,
    })


def teacher_form(request, pk=None):
    obj = get_object_or_404(Teacher, pk=pk) if pk else None
    form = TeacherForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Збережено.')
        return redirect('scheduler:teacher_list')
    return render(request, 'scheduler/teacher_form.html', {
        'form': form,
        'title': 'Редагувати вчителя' if obj else 'Додати вчителя',
        'back_url': 'scheduler:teacher_list',
        'days_labels': DAYS_LABELS,
        'max_periods': MAX_PERIODS,
        'periods_range': range(MAX_PERIODS),
        'days_range': range(5),
    })


def teacher_delete(request, pk):
    obj = get_object_or_404(Teacher, pk=pk)
    if request.method == 'POST':
        if Lesson.objects.filter(teacher=obj).exists():
            messages.error(request, 'Неможна видалити вчителя — він присутній у згенерованому розкладі.')
            return redirect('scheduler:teacher_list')
        obj.delete()
        messages.success(request, 'Вчителя видалено.')
        return redirect('scheduler:teacher_list')
    return render(request, 'scheduler/confirm_delete.html', {
        'obj': obj, 'back_url': 'scheduler:teacher_list',
    })


# ─── Subjects ────────────────────────────────────────────────────────────────

def subject_list(request):
    return render(request, 'scheduler/subject_list.html', {
        'subjects': Subject.objects.all(),
    })


def subject_form(request, pk=None):
    obj = get_object_or_404(Subject, pk=pk) if pk else None
    form = SubjectForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Збережено.')
        return redirect('scheduler:subject_list')
    return render(request, 'scheduler/form.html', {
        'form': form,
        'title': 'Редагувати предмет' if obj else 'Додати предмет',
        'back_url': 'scheduler:subject_list',
    })


def subject_delete(request, pk):
    obj = get_object_or_404(Subject, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Предмет видалено.')
        return redirect('scheduler:subject_list')
    return render(request, 'scheduler/confirm_delete.html', {
        'obj': obj, 'back_url': 'scheduler:subject_list',
    })


# ─── Rooms ───────────────────────────────────────────────────────────────────

def room_list(request):
    active = Schedule.objects.filter(is_active=True).first()
    return render(request, 'scheduler/room_list.html', {
        'rooms': Room.objects.select_related('subject').all(),
        'active_schedule': active,
    })


def room_form(request, pk=None):
    obj = get_object_or_404(Room, pk=pk) if pk else None
    form = RoomForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Збережено.')
        return redirect('scheduler:room_list')
    return render(request, 'scheduler/form.html', {
        'form': form,
        'title': 'Редагувати кабінет' if obj else 'Додати кабінет',
        'back_url': 'scheduler:room_list',
    })


def room_delete(request, pk):
    obj = get_object_or_404(Room, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Кабінет видалено.')
        return redirect('scheduler:room_list')
    return render(request, 'scheduler/confirm_delete.html', {
        'obj': obj, 'back_url': 'scheduler:room_list',
    })


# ─── Classes ─────────────────────────────────────────────────────────────────

def class_list(request):
    return render(request, 'scheduler/class_list.html', {
        'classes': SchoolClass.objects.select_related('home_room').all(),
    })


def class_form(request, pk=None):
    obj = get_object_or_404(SchoolClass, pk=pk) if pk else None
    form = SchoolClassForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Збережено.')
        return redirect('scheduler:class_list')
    return render(request, 'scheduler/form.html', {
        'form': form,
        'title': 'Редагувати клас' if obj else 'Додати клас',
        'back_url': 'scheduler:class_list',
    })


def class_delete(request, pk):
    obj = get_object_or_404(SchoolClass, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Клас видалено.')
        return redirect('scheduler:class_list')
    return render(request, 'scheduler/confirm_delete.html', {
        'obj': obj, 'back_url': 'scheduler:class_list',
    })


from django.forms import inlineformset_factory as _ifs

ClassLoadFormSet = _ifs(
    SchoolClass, TeacherSubject,
    form=ClassLoadRowForm,
    fk_name='school_class',
    extra=1,
    can_delete=True,
)


def class_load(request, pk):
    cls = get_object_or_404(SchoolClass, pk=pk)
    if request.method == 'POST':
        formset = ClassLoadFormSet(request.POST, instance=cls)
        if formset.is_valid():
            formset.save()
            messages.success(request, 'Навантаження збережено.')
            return redirect('scheduler:class_load', pk=cls.pk)
    else:
        formset = ClassLoadFormSet(instance=cls)

    # Підрахунок годин на тиждень (по слотах класу, не по кількості записів)
    all_ts = list(TeacherSubject.objects.filter(school_class=cls).order_by('subject__name', 'group'))
    seen = set()
    slot_hours = 0
    for ts in all_ts:
        key = (ts.subject_id, ts.group if ts.group else None)
        if ts.group is None or key not in seen:
            slot_hours += ts.hours_per_week
        seen.add(key)

    return render(request, 'scheduler/class_load.html', {
        'cls': cls,
        'formset': formset,
        'slot_hours': slot_hours,
    })


# ─── TeacherSubject (Навантаження) ───────────────────────────────────────────

def ts_list(request):
    qs = TeacherSubject.objects.select_related('teacher', 'subject', 'school_class').all()
    return render(request, 'scheduler/ts_list.html', {'assignments': qs})


def ts_form(request, pk=None):
    obj = get_object_or_404(TeacherSubject, pk=pk) if pk else None
    form = TeacherSubjectForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Збережено.')
        return redirect('scheduler:ts_list')
    return render(request, 'scheduler/form.html', {
        'form': form,
        'title': 'Редагувати навантаження' if obj else 'Додати навантаження',
        'back_url': 'scheduler:ts_list',
    })


def ts_delete(request, pk):
    obj = get_object_or_404(TeacherSubject, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Запис видалено.')
        return redirect('scheduler:ts_list')
    return render(request, 'scheduler/confirm_delete.html', {
        'obj': obj, 'back_url': 'scheduler:ts_list',
    })


TeacherLoadFormSet = inlineformset_factory(
    Teacher, TeacherSubject,
    form=TeacherLoadRowForm,
    extra=1,
    can_delete=True,
)


def teacher_load(request, teacher_pk):
    teacher = get_object_or_404(Teacher, pk=teacher_pk)
    if request.method == 'POST':
        formset = TeacherLoadFormSet(request.POST, instance=teacher)
        if formset.is_valid():
            formset.save()
            messages.success(request, 'Навантаження збережено.')
            return redirect('scheduler:teacher_load', teacher_pk=teacher.pk)
    else:
        formset = TeacherLoadFormSet(instance=teacher)
    total_hours = teacher.teachersubject_set.aggregate(
        total=models.Sum('hours_per_week')
    )['total'] or 0
    subject_totals = list(
        teacher.teachersubject_set
        .values('subject__name')
        .annotate(total=models.Sum('hours_per_week'))
        .order_by('subject__name')
    )
    return render(request, 'scheduler/teacher_load.html', {
        'teacher': teacher,
        'formset': formset,
        'total_hours': total_hours,
        'subject_totals': subject_totals,
    })


# ─── Bell schedules ──────────────────────────────────────────────────────────

BellPeriodFormSet = inlineformset_factory(
    BellSchedule, BellPeriod,
    form=BellPeriodForm,
    extra=1,
    can_delete=True,
)


def bell_list(request):
    return render(request, 'scheduler/bell_list.html', {
        'bells': BellSchedule.objects.prefetch_related('periods').all(),
    })


def bell_form(request, pk=None):
    obj = get_object_or_404(BellSchedule, pk=pk) if pk else None
    if request.method == 'POST':
        form = BellScheduleForm(request.POST, instance=obj)
        formset = BellPeriodFormSet(request.POST, instance=obj or BellSchedule())
        if form.is_valid() and formset.is_valid():
            saved = form.save()
            formset.instance = saved
            formset.save()
            messages.success(request, 'Збережено.')
            return redirect('scheduler:bell_edit', pk=saved.pk)
    else:
        form = BellScheduleForm(instance=obj)
        formset = BellPeriodFormSet(instance=obj or BellSchedule())
    return render(request, 'scheduler/bell_form.html', {
        'form': form,
        'formset': formset,
        'obj': obj,
    })


def bell_delete(request, pk):
    obj = get_object_or_404(BellSchedule, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Видалено.')
        return redirect('scheduler:bell_list')
    return render(request, 'scheduler/confirm_delete.html', {
        'obj': obj, 'back_url': 'scheduler:bell_list',
    })


# ─── Schedules ───────────────────────────────────────────────────────────────

def schedule_list(request):
    return render(request, 'scheduler/schedule_list.html', {
        'schedules': Schedule.objects.all(),
    })


def schedule_form(request, pk=None):
    obj = get_object_or_404(Schedule, pk=pk) if pk else None
    form = ScheduleForm(request.POST or None, instance=obj)
    if form.is_valid():
        form.save()
        messages.success(request, 'Збережено.')
        return redirect('scheduler:schedule_list')
    return render(request, 'scheduler/form.html', {
        'form': form,
        'title': 'Редагувати розклад' if obj else 'Новий розклад',
        'back_url': 'scheduler:schedule_list',
    })


def schedule_delete(request, pk):
    obj = get_object_or_404(Schedule, pk=pk)
    if request.method == 'POST':
        obj.delete()
        messages.success(request, 'Розклад видалено.')
        return redirect('scheduler:schedule_list')
    return render(request, 'scheduler/confirm_delete.html', {
        'obj': obj, 'back_url': 'scheduler:schedule_list',
    })


def _validate_move(schedule, lesson, new_day, new_period, swap=None):
    """Перевіряє переміщення уроку на (new_day, new_period). swap — урок з яким обмінюємось."""
    errors = []
    exclude = {lesson.pk}
    if swap:
        exclude.add(swap.pk)
    D = schedule.days_per_week
    week = lesson.week  # переміщення лише в межах одного тижня

    # 1. Доступність вчителя (день + слот)
    mask = lesson.teacher.available_days[:D].ljust(D, '0')
    if new_day >= D or mask[new_day] != '1':
        errors.append(f'Вчитель {lesson.teacher} недоступний у цей день')
    elif not lesson.teacher.is_slot_available(new_day, new_period):
        errors.append(f'Вчитель {lesson.teacher} недоступний на цьому уроці')

    # 2. Конфлікт вчителя
    if Lesson.objects.filter(
        schedule=schedule, teacher=lesson.teacher, week=week, day=new_day, period=new_period
    ).exclude(pk__in=exclude).exists():
        errors.append(f'Вчитель {lesson.teacher} вже має урок в цей час')

    # 3. Конфлікт класу (для обміну в тому ж класі слот не змінюється — пропускаємо)
    same_class_swap = swap and swap.school_class_id == lesson.school_class_id
    if not same_class_swap:
        existing_qs = Lesson.objects.filter(
            schedule=schedule, school_class=lesson.school_class, week=week, day=new_day, period=new_period
        ).exclude(pk__in=exclude)
        if lesson.group:
            # Груповий урок: дозволяємо іншу групу в тому ж слоті; забороняємо лише
            # якщо там є цілокласний урок або та сама група вже є.
            if existing_qs.filter(group__isnull=True).exists():
                errors.append(f'Клас {lesson.school_class}: цілокласний урок вже є в цей час')
            elif existing_qs.filter(group=lesson.group).exists():
                errors.append(f'Клас {lesson.school_class}: група {lesson.group} вже має урок в цей час')
        else:
            if existing_qs.exists():
                errors.append(f'Клас {lesson.school_class} вже має урок в цей час')

    # 4. Максимум уроків вчителя на день
    if lesson.day != new_day:
        cnt = Lesson.objects.filter(
            schedule=schedule, teacher=lesson.teacher, week=week, day=new_day
        ).exclude(pk__in=exclude).count() + 1
        if cnt > lesson.teacher.max_lessons_per_day:
            errors.append(f'Вчитель {lesson.teacher}: ліміт {lesson.teacher.max_lessons_per_day} ур/день буде перевищено')

    return errors


@require_POST
def lesson_move(request, pk):
    from django.http import JsonResponse
    import json
    schedule = get_object_or_404(Schedule, pk=pk)
    try:
        data = json.loads(request.body)
        lesson_id = data['lesson_id']
        new_day = int(data['new_day'])
        new_period = int(data['new_period'])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return JsonResponse({'ok': False, 'errors': ['Некоректний запит']}, status=400)
    lesson = get_object_or_404(Lesson, pk=lesson_id, schedule=schedule)

    # Урок в тій самій комірці — нічого не робимо
    if lesson.day == new_day and lesson.period == new_period:
        return JsonResponse({'ok': True})

    from django.db import transaction

    old_day, old_period = lesson.day, lesson.period

    # "Family" = всі записи того ж уроку по всіх тижнях.
    # Базовий урок (xb=1) зберігається двома рядками: week=0 і week=1 в одному слоті.
    # Рухаємо одразу всю сім'ю — інакше два тижні розповзуться в різні слоти.
    lesson_family_qs = Lesson.objects.filter(
        schedule=schedule,
        school_class=lesson.school_class,
        teacher=lesson.teacher,
        subject=lesson.subject,
        group=lesson.group,
        day=old_day,
        period=old_period,
    )

    # Знаходимо "swap": якщо передано target_lid — беремо конкретний урок (для вибору
    # групи з двогрупової комірки). Інакше — перший урок у цільовій комірці.
    target_lid = data.get('target_lid')
    if target_lid:
        try:
            swap = Lesson.objects.get(pk=int(target_lid), schedule=schedule)
            if (swap.day != new_day or swap.period != new_period
                    or swap.school_class_id != lesson.school_class_id):
                return JsonResponse({'ok': False, 'errors': ['Некоректний цільовий урок']}, status=400)
        except (Lesson.DoesNotExist, ValueError, TypeError):
            return JsonResponse({'ok': False, 'errors': ['Урок не знайдено']}, status=400)
    else:
        # Якщо target_lid не вказано — це чисте переміщення (move), а не обмін (swap).
        # Урок просто переноситься в новий слот; будь-які існуючі уроки там залишаються.
        swap = None

    errors = _validate_move(schedule, lesson, new_day, new_period, swap)
    if swap:
        errors += _validate_move(schedule, swap, old_day, old_period, lesson)
    if errors:
        return JsonResponse({'ok': False, 'errors': errors})

    swap_family_qs = None
    if swap:
        # Сім'я swap — всі тижні тієї ж (teacher, subject, group) в цільовому слоті
        swap_family_qs = Lesson.objects.filter(
            schedule=schedule,
            school_class=lesson.school_class,
            teacher=swap.teacher,
            subject=swap.subject,
            group=swap.group,
            day=new_day,
            period=new_period,
        )

    # Зберігаємо pk ДО будь-яких оновлень — querysets ледачі, і після step 1
    # (swap → temp) фільтр по old day/period вже нічого не знайде в step 3.
    lesson_pks = list(lesson_family_qs.values_list('pk', flat=True))
    swap_pks = list(swap_family_qs.values_list('pk', flat=True)) if swap_family_qs is not None else []

    # Three-step atomic swap через тимчасовий слот поза діапазоном розкладу.
    # SQLite перевіряє UNIQUE після кожного рядка, тому послідовні save() однакового
    # вчителя дають IntegrityError. filter().update() — прямий SQL без ORM-перевірок,
    # а тимчасовий (D, P) гарантовано вільний (поза 0..D-1 / 0..P-1).
    D = schedule.days_per_week
    P = schedule.lessons_per_day
    with transaction.atomic():
        if swap_pks:
            Lesson.objects.filter(pk__in=swap_pks).update(day=D, period=P)           # step 1: swap → temp
        Lesson.objects.filter(pk__in=lesson_pks).update(day=new_day, period=new_period)  # step 2: lesson → ціль
        if swap_pks:
            Lesson.objects.filter(pk__in=swap_pks).update(day=old_day, period=old_period)  # step 3: swap → джерело

    return JsonResponse({'ok': True})


def lesson_set_room(request, pk):
    from django.http import JsonResponse
    import json
    schedule = get_object_or_404(Schedule, pk=pk)

    if request.method == 'GET':
        lesson_id = request.GET.get('lesson_id')
        lesson = get_object_or_404(Lesson, pk=lesson_id, schedule=schedule)

        # Зайнятість кабінетів у цьому слоті, крім поточного уроку
        occupancy: dict = defaultdict(int)
        for l in (Lesson.objects
                  .filter(schedule=schedule, day=lesson.day, period=lesson.period, week=lesson.week)
                  .exclude(pk=lesson.pk)
                  .exclude(room__isnull=True)):
            occupancy[l.room_id] += 1

        rooms = Room.objects.select_related('subject').order_by('name')
        available = []
        for r in rooms:
            if occupancy.get(r.pk, 0) < r.max_simultaneous:
                available.append({
                    'id': r.pk,
                    'name': r.name,
                    'current': r.pk == lesson.room_id,
                })

        return JsonResponse({
            'current_room_id': lesson.room_id,
            'current_room_name': lesson.room.name if lesson.room else None,
            'rooms': available,
        })

    # POST — зберегти кабінет
    try:
        data = json.loads(request.body)
        lesson_id = data['lesson_id']
        room_id = data.get('room_id')  # None = без кабінету
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'Некоректний запит'}, status=400)

    lesson = get_object_or_404(Lesson, pk=lesson_id, schedule=schedule)

    if room_id is not None:
        room = get_object_or_404(Room, pk=room_id)
        used = (Lesson.objects
                .filter(schedule=schedule, day=lesson.day, period=lesson.period,
                        week=lesson.week, room=room)
                .exclude(pk=lesson.pk)
                .count())
        if used >= room.max_simultaneous:
            return JsonResponse({'ok': False, 'error': f'Кабінет {room.name} вже зайнятий у цей урок'})
        lesson.room = room
    else:
        lesson.room = None

    lesson.save(update_fields=['room'])
    return JsonResponse({'ok': True})


@require_POST
def schedule_generate(request, pk):
    from .generator import generate
    optimize_teachers = request.POST.get('optimize_teachers') == '1'
    ok, msg = generate(pk, optimize_teachers=optimize_teachers)
    if ok:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('scheduler:schedule_view', pk=pk)


def room_schedule(request, schedule_pk, room_pk):
    schedule = get_object_or_404(Schedule, pk=schedule_pk)
    room = get_object_or_404(Room, pk=room_pk)
    lessons = schedule.lessons.filter(room=room).select_related('school_class', 'subject', 'teacher')
    days = DAYS_FULL[:schedule.days_per_week]
    D = schedule.days_per_week
    P = schedule.lessons_per_day
    periods = range(P)

    # Collect raw lessons per slot
    raw: dict = defaultdict(list)
    for lesson in lessons:
        if lesson.day < D and lesson.period < P:
            raw[lesson.day, lesson.period].append(lesson)

    # grid[d][p] → list of entries, one per class in the slot.
    # Each entry: {'kind': 'regular'|'alt'|'week_a'|'week_b',
    #              'lesson': l,          ← for regular/week_a/week_b
    #              'week_a': l|None,     ← for alt
    #              'week_b': l|None}     ← for alt
    grid = {d: {p: [] for p in range(P)} for d in range(D)}
    for d in range(D):
        for p in range(P):
            cell = raw.get((d, p), [])
            if not cell:
                continue
            by_class: dict = defaultdict(list)
            for l in cell:
                by_class[l.school_class_id].append(l)
            entries = []
            for ls in by_class.values():
                week_a = next((l for l in ls if l.week == 0), None)
                week_b = next((l for l in ls if l.week == 1), None)
                if week_a and week_b and week_a.subject_id == week_b.subject_id:
                    entries.append({'kind': 'regular', 'lesson': week_a})
                elif week_a and week_b:
                    entries.append({'kind': 'alt', 'week_a': week_a, 'week_b': week_b})
                elif week_a:
                    entries.append({'kind': 'week_a', 'lesson': week_a})
                else:
                    entries.append({'kind': 'week_b', 'lesson': week_b})
            grid[d][p] = entries

    bell_times = {}
    if schedule.bell_schedule_id:
        bell_times = {
            bp.number - 1: bp
            for bp in BellPeriod.objects.filter(bell_schedule_id=schedule.bell_schedule_id)
        }

    all_rooms = Room.objects.all()
    return render(request, 'scheduler/room_schedule.html', {
        'schedule': schedule,
        'room': room,
        'all_rooms': all_rooms,
        'days': days,
        'periods': periods,
        'grid': grid,
        'bell_times': bell_times,
    })


def teacher_schedule(request, schedule_pk, teacher_pk):
    schedule = get_object_or_404(Schedule, pk=schedule_pk)
    teacher = get_object_or_404(Teacher, pk=teacher_pk)
    lessons = schedule.lessons.filter(teacher=teacher).select_related('school_class', 'subject', 'room')
    days = DAYS_FULL[:schedule.days_per_week]
    D = schedule.days_per_week
    P = schedule.lessons_per_day
    periods = range(P)

    # grid[d][p]: None | {'kind':'regular','lesson':l} | {'kind':'alt','week_a':l,'week_b':l}
    raw = {d: {p: [] for p in range(P)} for d in range(D)}
    for lesson in lessons:
        if lesson.day < D and lesson.period < P:
            raw[lesson.day][lesson.period].append(lesson)

    grid = {}
    for d in range(D):
        grid[d] = {}
        for p in range(P):
            cell = raw[d][p]
            if not cell:
                grid[d][p] = None
                continue
            week_a = [l for l in cell if l.week == 0]
            week_b = [l for l in cell if l.week == 1]
            subj_a = {l.subject_id for l in week_a}
            subj_b = {l.subject_id for l in week_b}
            if week_a and week_b and subj_a == subj_b:
                grid[d][p] = {'kind': 'regular', 'lesson': week_a[0]}
            elif week_a and week_b:
                grid[d][p] = {'kind': 'alt', 'week_a': week_a[0], 'week_b': week_b[0]}
            elif week_a:
                grid[d][p] = {'kind': 'week_a', 'lesson': week_a[0]}
            else:
                grid[d][p] = {'kind': 'week_b', 'lesson': week_b[0]}

    bell_times = {}
    if schedule.bell_schedule_id:
        bell_times = {
            bp.number - 1: bp
            for bp in BellPeriod.objects.filter(bell_schedule_id=schedule.bell_schedule_id)
        }

    all_teachers = Teacher.objects.all()
    return render(request, 'scheduler/teacher_schedule.html', {
        'schedule': schedule,
        'teacher': teacher,
        'all_teachers': all_teachers,
        'days': days,
        'periods': periods,
        'grid': grid,
        'bell_times': bell_times,
    })


def _build_display_grid(lessons, classes, D, P):
    """
    Повертає grid[class_pk][day][period] — dict з ключами:
      None                          → порожня комірка
      {'kind':'regular', 'primary': lesson, 'extra': lesson|None}
                                    → звичайний урок (або два записи однієї групи)
      {'kind':'alt', 'week_a': lesson|None, 'week_b': lesson|None}
                                    → уроки що чергуються по тижнях
    """
    raw = {sc.pk: {d: {p: [] for p in range(P)} for d in range(D)} for sc in classes}
    for lesson in lessons:
        if lesson.day < D and lesson.period < P:
            raw[lesson.school_class_id][lesson.day][lesson.period].append(lesson)

    grid = {}
    for sc in classes:
        grid[sc.pk] = {}
        for d in range(D):
            grid[sc.pk][d] = {}
            for p in range(P):
                cell = raw[sc.pk][d][p]
                if not cell:
                    grid[sc.pk][d][p] = None
                    continue
                week_a = [l for l in cell if l.week == 0]
                week_b = [l for l in cell if l.week == 1]
                subj_a = {l.subject_id for l in week_a}
                subj_b = {l.subject_id for l in week_b}
                if week_a and week_b and subj_a == subj_b:
                    # Однаковий предмет обидва тижні → звичайний (базовий) урок
                    primary = week_a[0]
                    extra = week_a[1] if len(week_a) > 1 else None
                    grid[sc.pk][d][p] = {'kind': 'regular', 'primary': primary, 'extra': extra}
                else:
                    # Або лише тиждень А (черговий 0.5г), або різні предмети → alt
                    grid[sc.pk][d][p] = {
                        'kind': 'alt',
                        'week_a': week_a[0] if week_a else None,
                        'week_b': week_b[0] if week_b else None,
                    }
    return grid


def schedule_view(request, pk):
    schedule = get_object_or_404(Schedule, pk=pk)
    lessons = schedule.lessons.select_related('school_class', 'subject', 'teacher', 'room').order_by('week', 'group')
    classes = SchoolClass.objects.all()
    D = schedule.days_per_week
    P = schedule.lessons_per_day
    days = DAYS_FULL[:D]
    periods = range(P)

    grid = _build_display_grid(lessons, classes, D, P)

    bell_times = {}
    if schedule.bell_schedule_id:
        bell_times = {
            bp.number - 1: bp
            for bp in BellPeriod.objects.filter(bell_schedule_id=schedule.bell_schedule_id)
        }

    return render(request, 'scheduler/schedule_view.html', {
        'schedule': schedule,
        'classes': classes,
        'days': days,
        'periods': periods,
        'grid': grid,
        'all_teachers': Teacher.objects.all(),
        'bell_times': bell_times,
    })


# ─── Exports ─────────────────────────────────────────────────────────────────

def export_teacher_load(request):
    """XLSX: all teachers and their subject load per class."""
    import io
    from django.http import HttpResponse
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Навантаження вчителів'

    # ── Styles ──
    hdr_font   = Font(bold=True, color='FFFFFF', size=11)
    hdr_fill   = PatternFill('solid', fgColor='2C5F8A')
    sub_font   = Font(bold=True, size=10)
    sub_fill   = PatternFill('solid', fgColor='D6E4F0')
    total_font = Font(bold=True, size=10)
    total_fill = PatternFill('solid', fgColor='EAF4EA')
    center     = Alignment(horizontal='center', vertical='center')
    thin       = Side(style='thin', color='BFBFBF')
    border     = Border(left=thin, right=thin, top=thin, bottom=thin)

    def cell(row, col, value, font=None, fill=None, align=None, num_fmt=None):
        c = ws.cell(row=row, column=col, value=value)
        if font:   c.font      = font
        if fill:   c.fill      = fill
        if align:  c.alignment = align
        if num_fmt: c.number_format = num_fmt
        c.border = border
        return c

    # ── Header row ──
    headers = ['Вчитель', 'Предмет', 'Клас', 'Група', 'Год/тиж']
    for col, h in enumerate(headers, 1):
        cell(1, col, h, font=hdr_font, fill=hdr_fill, align=center)
    ws.row_dimensions[1].height = 20

    # ── Data ──
    assignments = (
        TeacherSubject.objects
        .select_related('teacher', 'subject', 'school_class')
        .order_by('teacher__last_name', 'teacher__first_name',
                  'school_class__grade', 'school_class__letter',
                  'subject__name', 'group')
    )

    row = 2
    current_teacher = None
    teacher_start   = 2
    teacher_total   = 0

    for ts in assignments:
        teacher_name = str(ts.teacher)

        # Teacher subtotal row when teacher changes
        if current_teacher is not None and teacher_name != current_teacher:
            cell(row, 1, f'Разом: {current_teacher}', font=total_font, fill=total_fill)
            cell(row, 2, '', fill=total_fill)
            cell(row, 3, '', fill=total_fill)
            cell(row, 4, '', fill=total_fill)
            cell(row, 5, teacher_total, font=total_font, fill=total_fill,
                 align=center, num_fmt='0.0')
            ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
            row += 1
            teacher_total = 0

        current_teacher = teacher_name
        teacher_total  += ts.hours_per_week

        cell(row, 1, teacher_name)
        cell(row, 2, ts.subject.name)
        cell(row, 3, str(ts.school_class), align=center)
        cell(row, 4, ts.group if ts.group else '', align=center)
        cell(row, 5, float(ts.hours_per_week), align=center, num_fmt='0.0')
        row += 1

    # Last teacher subtotal
    if current_teacher:
        cell(row, 1, f'Разом: {current_teacher}', font=total_font, fill=total_fill)
        cell(row, 2, '', fill=total_fill)
        cell(row, 3, '', fill=total_fill)
        cell(row, 4, '', fill=total_fill)
        cell(row, 5, teacher_total, font=total_font, fill=total_fill,
             align=center, num_fmt='0.0')
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        row += 1

    # Grand total
    grand_total = float(TeacherSubject.objects.aggregate(
        s=models.Sum('hours_per_week')
    )['s'] or 0)
    gt_font = Font(bold=True, color='FFFFFF', size=11)
    gt_fill = PatternFill('solid', fgColor='2C5F8A')
    cell(row, 1, 'ЗАГАЛОМ', font=gt_font, fill=gt_fill, align=center)
    cell(row, 2, '', font=gt_font, fill=gt_fill)
    cell(row, 3, '', font=gt_font, fill=gt_fill)
    cell(row, 4, '', font=gt_font, fill=gt_fill)
    cell(row, 5, grand_total, font=gt_font, fill=gt_fill, align=center, num_fmt='0.0')
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)

    # ── Column widths ──
    ws.column_dimensions['A'].width = 28
    ws.column_dimensions['B'].width = 26
    ws.column_dimensions['C'].width = 8
    ws.column_dimensions['D'].width = 8
    ws.column_dimensions['E'].width = 12

    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="teacher_load.xlsx"'
    return resp


def export_class_load(request):
    """XLSX: all classes with subjects, teachers, groups and hours."""
    import io
    from django.http import HttpResponse
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Навантаження по класах'

    # ── Styles ──
    hdr_font   = Font(bold=True, color='FFFFFF', size=11)
    hdr_fill   = PatternFill('solid', fgColor='2C5F8A')
    total_font = Font(bold=True, size=10)
    total_fill = PatternFill('solid', fgColor='EAF4EA')
    center     = Alignment(horizontal='center', vertical='center')
    thin       = Side(style='thin', color='BFBFBF')
    border     = Border(left=thin, right=thin, top=thin, bottom=thin)

    def cell(row, col, value, font=None, fill=None, align=None, num_fmt=None):
        c = ws.cell(row=row, column=col, value=value)
        if font:    c.font   = font
        if fill:    c.fill   = fill
        if align:   c.alignment = align
        if num_fmt: c.number_format = num_fmt
        c.border = border
        return c

    # ── Header ──
    headers = ['Клас', 'Предмет', 'Вчитель', 'Група', 'Год/тиж']
    for col, h in enumerate(headers, 1):
        cell(1, col, h, font=hdr_font, fill=hdr_fill, align=center)
    ws.row_dimensions[1].height = 20

    # ── Pre-compute merged total per class (grouped subjects counted once) ──
    # For each (class, subject) pair take the max hours across groups, then sum.
    from django.db.models import Max
    merged_by_class: dict = defaultdict(float)
    for row_agg in (TeacherSubject.objects
                    .values('school_class_id', 'subject_id')
                    .annotate(h=Max('hours_per_week'))):
        merged_by_class[row_agg['school_class_id']] += float(row_agg['h'])

    # ── Data ──
    assignments = (
        TeacherSubject.objects
        .select_related('teacher', 'subject', 'school_class')
        .order_by('school_class__grade', 'school_class__letter',
                  'subject__name', 'group', 'teacher__last_name')
    )

    merge2_fill = PatternFill('solid', fgColor='D5E8D4')  # slightly different green

    def write_class_subtotal(r, cls_name, cls_pk, raw_total):
        merged = merged_by_class.get(cls_pk, raw_total)
        # Row 1: full sum (groups counted separately)
        cell(r, 1, f'Разом (з групами): {cls_name}', font=total_font, fill=total_fill)
        for c in range(2, 5): cell(r, c, '', fill=total_fill)
        cell(r, 5, raw_total, font=total_font, fill=total_fill, align=center, num_fmt='0.0')
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        r += 1
        # Row 2: merged sum (group duplicates removed)
        cell(r, 1, f'Разом (без груп): {cls_name}', font=total_font, fill=merge2_fill)
        for c in range(2, 5): cell(r, c, '', fill=merge2_fill)
        cell(r, 5, merged, font=total_font, fill=merge2_fill, align=center, num_fmt='0.0')
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=4)
        return r + 1

    row           = 2
    current_class = None
    current_cls_pk = None
    class_total   = 0

    for ts in assignments:
        cls_name = str(ts.school_class)

        if current_class is not None and cls_name != current_class:
            row = write_class_subtotal(row, current_class, current_cls_pk, class_total)
            class_total = 0

        current_class  = cls_name
        current_cls_pk = ts.school_class_id
        class_total   += ts.hours_per_week

        cell(row, 1, cls_name, align=center)
        cell(row, 2, ts.subject.name)
        cell(row, 3, str(ts.teacher))
        cell(row, 4, ts.group if ts.group else '', align=center)
        cell(row, 5, float(ts.hours_per_week), align=center, num_fmt='0.0')
        row += 1

    # Last class subtotal
    if current_class:
        row = write_class_subtotal(row, current_class, current_cls_pk, class_total)

    # Grand totals
    grand_total = float(TeacherSubject.objects.aggregate(
        s=models.Sum('hours_per_week')
    )['s'] or 0)
    grand_merged = sum(merged_by_class.values())
    gt_font = Font(bold=True, color='FFFFFF', size=11)
    gt_fill = PatternFill('solid', fgColor='2C5F8A')
    for label, value in [('ЗАГАЛОМ (з групами)', grand_total),
                          ('ЗАГАЛОМ (без груп)', grand_merged)]:
        cell(row, 1, label, font=gt_font, fill=gt_fill, align=center)
        for c in range(2, 5): cell(row, c, '', font=gt_font, fill=gt_fill)
        cell(row, 5, value, font=gt_font, fill=gt_fill, align=center, num_fmt='0.0')
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        row += 1

    # ── Column widths ──
    ws.column_dimensions['A'].width = 8
    ws.column_dimensions['B'].width = 26
    ws.column_dimensions['C'].width = 28
    ws.column_dimensions['D'].width = 8
    ws.column_dimensions['E'].width = 12

    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    resp['Content-Disposition'] = 'attachment; filename="class_load.xlsx"'
    return resp
