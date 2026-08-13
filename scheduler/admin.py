from django.contrib import admin
from .models import Teacher, Subject, Room, SchoolClass, TeacherSubject, Schedule, Lesson


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ['last_name', 'first_name', 'available_days', 'max_lessons_per_day']
    search_fields = ['last_name', 'first_name']


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ['name', 'short_name', 'can_be_double']


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['name', 'subject', 'capacity']


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'grade', 'letter', 'home_room']


@admin.register(TeacherSubject)
class TeacherSubjectAdmin(admin.ModelAdmin):
    list_display = ['teacher', 'subject', 'school_class', 'hours_per_week']
    list_filter = ['teacher', 'subject', 'school_class']


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ['name', 'days_per_week', 'lessons_per_day', 'is_active', 'created_at']


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ['schedule', 'school_class', 'subject', 'teacher', 'room', 'day', 'period', 'is_double']
    list_filter = ['schedule', 'school_class', 'teacher', 'day']
