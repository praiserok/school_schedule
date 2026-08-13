from django.db import models


class Teacher(models.Model):
    first_name = models.CharField(max_length=100, verbose_name='Імʼя')
    last_name = models.CharField(max_length=100, verbose_name='Прізвище')
    # Дні коли вчитель доступний, напр. "11110" = пн-чт
    available_days = models.CharField(
        max_length=6, default='11111',
        verbose_name='Доступні дні (пн-сб, 1=так)',
        help_text='5 або 6 символів 0/1, наприклад 11110 = пн-пт без пʼятниці',
    )
    max_lessons_per_day = models.PositiveSmallIntegerField(
        default=8, verbose_name='Макс. уроків на день'
    )

    class Meta:
        ordering = ['last_name', 'first_name']
        verbose_name = 'Вчитель'
        verbose_name_plural = 'Вчителі'

    def __str__(self):
        return f'{self.last_name} {self.first_name}'


class Subject(models.Model):
    name = models.CharField(max_length=100, verbose_name='Назва')
    short_name = models.CharField(max_length=20, blank=True, verbose_name='Скорочення')
    can_be_double = models.BooleanField(default=False, verbose_name='Може бути парним уроком')

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

    class Meta:
        ordering = ['name']
        verbose_name = 'Кабінет'
        verbose_name_plural = 'Кабінети'

    def __str__(self):
        return self.name


class SchoolClass(models.Model):
    grade = models.PositiveSmallIntegerField(verbose_name='Паралель')   # 1–11
    letter = models.CharField(max_length=2, verbose_name='Літера')      # А, Б, В…
    home_room = models.ForeignKey(
        Room, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Основний кабінет',
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
    hours_per_week = models.PositiveSmallIntegerField(verbose_name='Годин на тиждень')

    class Meta:
        unique_together = [('teacher', 'subject', 'school_class')]
        verbose_name = 'Навантаження'
        verbose_name_plural = 'Навантаження'

    def __str__(self):
        return f'{self.teacher} — {self.subject} — {self.school_class} ({self.hours_per_week}г)'


class Schedule(models.Model):
    """Заголовок згенерованого розкладу."""
    name = models.CharField(max_length=100, verbose_name='Назва')
    created_at = models.DateTimeField(auto_now_add=True)
    days_per_week = models.PositiveSmallIntegerField(default=5, verbose_name='Днів на тиждень')
    lessons_per_day = models.PositiveSmallIntegerField(default=7, verbose_name='Уроків на день')
    is_active = models.BooleanField(default=False, verbose_name='Активний')

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
    is_double = models.BooleanField(default=False)  # True — перший урок пари

    class Meta:
        unique_together = [
            ('schedule', 'school_class', 'day', 'period'),
            ('schedule', 'teacher', 'day', 'period'),
            ('schedule', 'room', 'day', 'period'),
        ]
        ordering = ['day', 'period', 'school_class']
        verbose_name = 'Урок'
        verbose_name_plural = 'Уроки'

    def __str__(self):
        return f'{self.school_class} {self.subject} д{self.day+1} у{self.period+1}'
