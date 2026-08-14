from django.db import models


class Teacher(models.Model):
    first_name = models.CharField(max_length=100, verbose_name='Імʼя')
    last_name = models.CharField(max_length=100, verbose_name='Прізвище')
    # Дні коли вчитель доступний, напр. "11110" = пн-чт
    available_days = models.CharField(
        max_length=5, default='11111',
        verbose_name='Доступні дні (пн-пт, 1=так)',
        help_text='5 символів 0/1, наприклад 11110 = пн-чт без пʼятниці',
    )
    max_lessons_per_day = models.PositiveSmallIntegerField(
        default=8, verbose_name='Макс. уроків на день'
    )

    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = 'Вчитель'
        verbose_name_plural = 'Вчителі'

    _DAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт']

    @property
    def days_display(self):
        days = self.available_days.ljust(5, '0')
        return [(label, days[i] == '1') for i, label in enumerate(self._DAY_LABELS)]

    def __str__(self):
        return f'{self.last_name} {self.first_name}'


class Subject(models.Model):
    name = models.CharField(max_length=100, verbose_name='Назва')
    short_name = models.CharField(max_length=20, blank=True, verbose_name='Скорочення')
    color = models.CharField(max_length=7, default='#6c757d', verbose_name='Колір')
    can_be_double = models.BooleanField(default=False, verbose_name='Може бути парним уроком')
    allow_shared_room = models.BooleanField(
        default=False, verbose_name='Дозволити спільний кабінет',
        help_text='Наприклад, фізкультура: два класи одночасно в спортзалі',
    )
    max_grade_diff = models.PositiveSmallIntegerField(
        default=2, verbose_name='Макс. різниця паралелей при спільному кабінеті',
        help_text='1 = тільки та сама або сусідня паралель, 2 = ±2 класи тощо',
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предмети'

    def __str__(self):
        return self.name


class Room(models.Model):
    name = models.CharField(max_length=50, verbose_name='Назва / номер')
    # Якщо задано — кабінет тільки для цього предмету (спортзал, хімія тощо)
    subject = models.ForeignKey(
        Subject, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Тільки для предмету',
    )
    capacity = models.PositiveSmallIntegerField(default=35, verbose_name='Місткість')
    max_simultaneous = models.PositiveSmallIntegerField(
        default=1, verbose_name='Макс. класів одночасно',
        help_text='Для спортзалу: 2, якщо можна вести два класи одночасно',
    )

    class Meta:
        ordering = ['name']
        verbose_name = 'Кабінет'
        verbose_name_plural = 'Кабінети'

    def __str__(self):
        return self.name


class SchoolClass(models.Model):
    grade = models.PositiveSmallIntegerField(verbose_name='Паралель')   # 1–11
    letter = models.CharField(max_length=2, blank=True, verbose_name='Літера')  # А, Б, В… або порожньо
    home_room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Основний кабінет',
        help_text='Для 1-4 класів: кабінет де проходять більшість уроків',
    )

    class Meta:
        ordering = ['grade', 'letter']
        unique_together = [('grade', 'letter')]
        verbose_name = 'Клас'
        verbose_name_plural = 'Класи'

    def __str__(self):
        return f'{self.grade}{self.letter}'


class TeacherSubject(models.Model):
    """Який вчитель веде який предмет в якому класі і скільки годин на тиждень."""
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, verbose_name='Вчитель')
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, verbose_name='Предмет')
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, verbose_name='Клас')
    hours_per_week = models.DecimalField(
        max_digits=4, decimal_places=1, verbose_name='Годин на тиждень',
        help_text='Дробові значення (0.5, 1.5, 3.5…) = урок раз на два тижні',
    )
    group = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name='Група',
        help_text='1, 2, … якщо клас ділиться на групи для цього предмету. Порожньо = весь клас',
    )

    class Meta:
        unique_together = [('teacher', 'subject', 'school_class', 'group')]
        verbose_name = 'Навантаження'
        verbose_name_plural = 'Навантаження'

    def __str__(self):
        grp = f' гр.{self.group}' if self.group else ''
        return f'{self.teacher} — {self.subject}{grp} — {self.school_class} ({self.hours_per_week}г)'


class BellSchedule(models.Model):
    """Розклад дзвінків — набір часів для кожного уроку."""
    name = models.CharField(max_length=100, verbose_name='Назва')

    class Meta:
        ordering = ['name']
        verbose_name = 'Розклад дзвінків'
        verbose_name_plural = 'Розклади дзвінків'

    def __str__(self):
        return self.name


class BellPeriod(models.Model):
    """Час одного уроку в розкладі дзвінків."""
    bell_schedule = models.ForeignKey(
        BellSchedule, on_delete=models.CASCADE, related_name='periods',
    )
    number = models.PositiveSmallIntegerField(verbose_name='Урок №')
    start_time = models.TimeField(verbose_name='Початок')
    end_time = models.TimeField(verbose_name='Кінець')

    class Meta:
        ordering = ['number']
        unique_together = [('bell_schedule', 'number')]
        verbose_name = 'Час уроку'
        verbose_name_plural = 'Часи уроків'

    @property
    def duration_minutes(self):
        from datetime import date, datetime
        start = datetime.combine(date.today(), self.start_time)
        end = datetime.combine(date.today(), self.end_time)
        return int((end - start).total_seconds() // 60)

    def __str__(self):
        return f'{self.number}. {self.start_time.strftime("%H:%M")}–{self.end_time.strftime("%H:%M")}'


class Schedule(models.Model):
    """Заголовок згенерованого розкладу."""
    name = models.CharField(max_length=100, verbose_name='Назва')
    created_at = models.DateTimeField(auto_now_add=True)
    days_per_week = models.PositiveSmallIntegerField(default=5, verbose_name='Днів на тиждень')
    lessons_per_day = models.PositiveSmallIntegerField(default=7, verbose_name='Уроків на день')
    is_active = models.BooleanField(default=False, verbose_name='Активний')
    bell_schedule = models.ForeignKey(
        BellSchedule, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Розклад дзвінків',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Розклад'
        verbose_name_plural = 'Розклади'

    def __str__(self):
        return self.name


class Lesson(models.Model):
    """Один урок у розкладі."""
    schedule = models.ForeignKey(Schedule, on_delete=models.CASCADE, related_name='lessons')
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, null=True, blank=True, on_delete=models.SET_NULL)
    day = models.PositiveSmallIntegerField()      # 0=пн … 4=пт
    period = models.PositiveSmallIntegerField()   # номер уроку (0-based)
    week = models.PositiveSmallIntegerField(default=0, db_index=True)  # 0=тиждень А, 1=тиждень Б
    is_double = models.BooleanField(default=False)  # True — перший урок пари

    class Meta:
        unique_together = [
            ('schedule', 'school_class', 'day', 'period', 'week'),
            ('schedule', 'teacher', 'day', 'period', 'week'),
        ]
        ordering = ['day', 'period', 'school_class']
        verbose_name = 'Урок'
        verbose_name_plural = 'Уроки'

    def __str__(self):
        w = 'Б' if self.week == 1 else 'А'
        return f'{self.school_class} {self.subject} тиж{w} д{self.day+1} у{self.period+1}'
