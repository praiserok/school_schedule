import re
from django import forms
from .models import Teacher, Subject, Room, SchoolClass, TeacherSubject, Schedule, BellSchedule, BellPeriod

DAYS_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт']
DAYS_FULL   = ['Понеділок', 'Вівторок', 'Середа', 'Четвер', 'Пʼятниця']
_ctrl = {'class': 'form-control'}
_sel = {'class': 'form-select'}


DAYS_CHOICES = [(str(i), label) for i, label in enumerate(DAYS_LABELS)]


class AvailableDaysField(forms.MultipleChoiceField):
    """Stores available days as a bitmask string like '111100'."""
    widget = forms.CheckboxSelectMultiple

    def __init__(self, **kwargs):
        kwargs.setdefault('choices', DAYS_CHOICES)
        kwargs.setdefault('required', False)
        super().__init__(**kwargs)

    def prepare_value(self, value):
        # Convert bitmask string → list of selected indices for initial display
        if isinstance(value, str) and len(value) >= 5:
            return [str(i) for i, c in enumerate(value) if c == '1']
        return super().prepare_value(value)

    def clean(self, value):
        selected = super().clean(value or [])
        bits = ['0'] * 5
        for idx in selected:
            bits[int(idx)] = '1'
        return ''.join(bits)


class TeacherForm(forms.ModelForm):
    available_days = AvailableDaysField(label='Доступні дні')

    class Meta:
        model = Teacher
        fields = ['last_name', 'first_name', 'available_days', 'max_lessons_per_day']
        labels = {
            'last_name': 'Прізвище',
            'first_name': 'Імʼя',
            'max_lessons_per_day': 'Макс. уроків на день',
        }
        widgets = {
            'last_name': forms.TextInput(attrs=_ctrl),
            'first_name': forms.TextInput(attrs=_ctrl),
            'max_lessons_per_day': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 10}),
        }

    def clean_max_lessons_per_day(self):
        v = self.cleaned_data.get('max_lessons_per_day')
        if v is not None and v < 1:
            raise forms.ValidationError('Має бути не менше 1')
        return v


class SubjectForm(forms.ModelForm):
    def clean_color(self):
        color = self.cleaned_data.get('color', '')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise forms.ValidationError('Колір повинен бути у форматі #RRGGBB')
        return color.lower()

    class Meta:
        model = Subject
        fields = ['name', 'short_name', 'color', 'can_be_double', 'allow_shared_room', 'max_grade_diff']
        labels = {
            'name': 'Назва',
            'short_name': 'Скорочення',
            'color': 'Колір',
            'can_be_double': 'Може бути парним уроком',
            'allow_shared_room': 'Дозволити спільний кабінет',
            'max_grade_diff': 'Макс. різниця паралелей',
        }
        widgets = {
            'name': forms.TextInput(attrs=_ctrl),
            'short_name': forms.TextInput(attrs=_ctrl),
            'color': forms.TextInput(attrs={'type': 'color', 'class': 'form-control form-control-color', 'style': 'width:56px;height:38px;padding:2px 4px;'}),
            'max_grade_diff': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 10}),
        }


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ['name', 'subject', 'capacity', 'max_simultaneous']
        labels = {
            'name': 'Назва / номер',
            'subject': 'Тільки для предмету (необов\'язково)',
            'capacity': 'Місткість',
            'max_simultaneous': 'Макс. класів одночасно',
        }
        widgets = {
            'name': forms.TextInput(attrs=_ctrl),
            'subject': forms.Select(attrs=_sel),
            'capacity': forms.NumberInput(attrs={**_ctrl, 'min': 1}),
            'max_simultaneous': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 10}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subject'].empty_label = '— будь-який предмет —'
        self.fields['subject'].required = False


class SchoolClassForm(forms.ModelForm):
    class Meta:
        model = SchoolClass
        fields = ['grade', 'letter', 'home_room']
        labels = {
            'grade': 'Паралель',
            'letter': 'Літера (необов\'язково)',
            'home_room': 'Основний кабінет (для 1-4 класів)',
        }
        widgets = {
            'grade': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 11}),
            'letter': forms.TextInput(attrs={**_ctrl, 'placeholder': 'А, Б, В… або порожньо'}),
            'home_room': forms.Select(attrs=_sel),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['home_room'].empty_label = '— не вказано —'
        self.fields['home_room'].required = False


class HoursValidationMixin:
    def clean_hours_per_week(self):
        v = self.cleaned_data.get('hours_per_week')
        if v is not None and v <= 0:
            raise forms.ValidationError('Має бути більше 0')
        return v


class TeacherSubjectForm(HoursValidationMixin, forms.ModelForm):
    class Meta:
        model = TeacherSubject
        fields = ['teacher', 'subject', 'school_class', 'hours_per_week']
        labels = {
            'teacher': 'Вчитель',
            'subject': 'Предмет',
            'school_class': 'Клас',
            'hours_per_week': 'Годин на тиждень',
        }
        widgets = {
            'teacher': forms.Select(attrs=_sel),
            'subject': forms.Select(attrs=_sel),
            'school_class': forms.Select(attrs=_sel),
            'hours_per_week': forms.NumberInput(attrs={**_ctrl, 'min': 0.5, 'max': 20, 'step': '0.5'}),
        }


class TeacherLoadRowForm(HoursValidationMixin, forms.ModelForm):
    """Один рядок навантаження вчителя (без поля teacher — воно встановлюється у view)."""
    class Meta:
        model = TeacherSubject
        fields = ['subject', 'school_class', 'hours_per_week', 'group']
        widgets = {
            'subject': forms.Select(attrs=_sel),
            'school_class': forms.Select(attrs=_sel),
            'hours_per_week': forms.NumberInput(attrs={**_ctrl, 'min': 0.5, 'max': 20, 'step': '0.5', 'style': 'width:72px;'}),
            'group': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 9, 'style': 'width:60px;', 'placeholder': '—'}),
        }


class ClassLoadRowForm(HoursValidationMixin, forms.ModelForm):
    """Один рядок навантаження класу (без поля school_class — береться з URL)."""
    class Meta:
        model = TeacherSubject
        fields = ['subject', 'teacher', 'hours_per_week', 'group']
        widgets = {
            'subject': forms.Select(attrs=_sel),
            'teacher': forms.Select(attrs=_sel),
            'hours_per_week': forms.NumberInput(attrs={**_ctrl, 'min': 0.5, 'max': 20, 'step': '0.5', 'style': 'width:72px;'}),
            'group': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 9, 'style': 'width:60px;', 'placeholder': '—'}),
        }


class ScheduleForm(forms.ModelForm):
    class Meta:
        model = Schedule
        fields = ['name', 'days_per_week', 'lessons_per_day', 'bell_schedule', 'is_active']
        labels = {
            'name': 'Назва',
            'days_per_week': 'Днів на тиждень',
            'lessons_per_day': 'Уроків на день',
            'bell_schedule': 'Розклад дзвінків',
            'is_active': 'Активний',
        }
        widgets = {
            'name': forms.TextInput(attrs=_ctrl),
            'days_per_week': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 5}),
            'lessons_per_day': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 10}),
            'bell_schedule': forms.Select(attrs=_sel),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['bell_schedule'].empty_label = '— без часів —'
        self.fields['bell_schedule'].required = False


class BellScheduleForm(forms.ModelForm):
    class Meta:
        model = BellSchedule
        fields = ['name']
        labels = {'name': 'Назва'}
        widgets = {'name': forms.TextInput(attrs=_ctrl)}


class BellPeriodForm(forms.ModelForm):
    class Meta:
        model = BellPeriod
        fields = ['number', 'start_time', 'end_time']
        widgets = {
            'number': forms.NumberInput(attrs={**_ctrl, 'min': 1, 'max': 20, 'style': 'width:64px;'}),
            'start_time': forms.TimeInput(attrs={**_ctrl, 'type': 'time', 'style': 'width:110px;'}),
            'end_time': forms.TimeInput(attrs={**_ctrl, 'type': 'time', 'style': 'width:110px;'}),
        }
