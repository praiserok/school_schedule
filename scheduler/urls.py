from django.urls import path
from . import views

app_name = 'scheduler'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('help/', views.help_page, name='help'),

    # Teachers
    path('teachers/', views.teacher_list, name='teacher_list'),
    path('teachers/add/', views.teacher_form, name='teacher_add'),
    path('teachers/<int:pk>/edit/', views.teacher_form, name='teacher_edit'),
    path('teachers/<int:pk>/delete/', views.teacher_delete, name='teacher_delete'),

    # Subjects
    path('subjects/', views.subject_list, name='subject_list'),
    path('subjects/add/', views.subject_form, name='subject_add'),
    path('subjects/<int:pk>/edit/', views.subject_form, name='subject_edit'),
    path('subjects/<int:pk>/delete/', views.subject_delete, name='subject_delete'),

    # Rooms
    path('rooms/', views.room_list, name='room_list'),
    path('rooms/add/', views.room_form, name='room_add'),
    path('rooms/<int:pk>/edit/', views.room_form, name='room_edit'),
    path('rooms/<int:pk>/delete/', views.room_delete, name='room_delete'),
    path('schedules/<int:schedule_pk>/room/<int:room_pk>/', views.room_schedule, name='room_schedule'),

    # Classes
    path('classes/', views.class_list, name='class_list'),
    path('classes/add/', views.class_form, name='class_add'),
    path('classes/<int:pk>/edit/', views.class_form, name='class_edit'),
    path('classes/<int:pk>/delete/', views.class_delete, name='class_delete'),
    path('classes/<int:pk>/load/', views.class_load, name='class_load'),

    # TeacherSubject assignments
    path('assignments/', views.ts_list, name='ts_list'),
    path('assignments/add/', views.ts_form, name='ts_add'),
    path('assignments/<int:pk>/edit/', views.ts_form, name='ts_edit'),
    path('assignments/<int:pk>/delete/', views.ts_delete, name='ts_delete'),
    path('assignments/teacher/<int:teacher_pk>/', views.teacher_load, name='teacher_load'),

    # Bell schedules
    path('bells/', views.bell_list, name='bell_list'),
    path('bells/add/', views.bell_form, name='bell_add'),
    path('bells/<int:pk>/edit/', views.bell_form, name='bell_edit'),
    path('bells/<int:pk>/delete/', views.bell_delete, name='bell_delete'),

    # Schedules
    path('schedules/', views.schedule_list, name='schedule_list'),
    path('schedules/add/', views.schedule_form, name='schedule_add'),
    path('schedules/<int:pk>/edit/', views.schedule_form, name='schedule_edit'),
    path('schedules/<int:pk>/delete/', views.schedule_delete, name='schedule_delete'),
    path('schedules/<int:pk>/', views.schedule_view, name='schedule_view'),
    path('schedules/<int:pk>/copy/', views.schedule_copy, name='schedule_copy'),
    path('schedules/<int:pk>/reset-rooms/', views.schedule_reset_rooms, name='schedule_reset_rooms'),
    path('schedules/<int:pk>/assign-rooms/', views.schedule_assign_rooms, name='schedule_assign_rooms'),
    path('schedules/<int:pk>/generate/', views.schedule_generate, name='schedule_generate'),
    path('schedules/<int:schedule_pk>/teacher/<int:teacher_pk>/', views.teacher_schedule, name='teacher_schedule'),
    path('schedules/<int:pk>/move-lesson/', views.lesson_move, name='lesson_move'),
    path('schedules/<int:pk>/set-room/', views.lesson_set_room, name='lesson_set_room'),
    path('schedules/<int:pk>/lesson-sibling/', views.lesson_sibling, name='lesson_sibling'),
    path('schedules/<int:pk>/toggle-week/', views.lesson_toggle_week, name='lesson_toggle_week'),
    path('schedules/<int:pk>/slot-lessons/', views.slot_lessons, name='slot_lessons'),
    path('schedules/<int:pk>/assign-teacher-room/', views.assign_teacher_to_room, name='assign_teacher_room'),
    path('schedules/<int:pk>/unassigned/', views.schedule_unassigned, name='schedule_unassigned'),
    path('schedules/<int:pk>/unassigned-count/', views.schedule_unassigned_count, name='schedule_unassigned_count'),

    # Public (no login required)
    path('public/', views.public_home, name='public_home'),
    path('public/class/<int:pk>/', views.public_class, name='public_class'),
    path('public/class/<int:pk>/export/', views.public_class_export, name='public_class_export'),
    path('public/teacher/<int:pk>/', views.public_teacher, name='public_teacher'),
    path('public/teacher/<int:pk>/export/', views.public_teacher_export, name='public_teacher_export'),
    path('public/room/<int:pk>/', views.public_room, name='public_room'),
    path('public/room/<int:pk>/export/', views.public_room_export, name='public_room_export'),

    # Exports
    path('export/teacher-load/', views.export_teacher_load, name='export_teacher_load'),
    path('export/class-load/', views.export_class_load, name='export_class_load'),
    path('schedules/<int:pk>/export/', views.export_schedule, name='export_schedule'),
    path('schedules/<int:pk>/export-rooms/', views.export_rooms, name='export_rooms'),
    path('schedules/<int:pk>/export-teacher-timetable/', views.export_teacher_timetable, name='export_teacher_timetable'),
]
