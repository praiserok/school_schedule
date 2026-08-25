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
    path('schedules/<int:pk>/generate/', views.schedule_generate, name='schedule_generate'),
    path('schedules/<int:schedule_pk>/teacher/<int:teacher_pk>/', views.teacher_schedule, name='teacher_schedule'),
    path('schedules/<int:pk>/move-lesson/', views.lesson_move, name='lesson_move'),

    # Exports
    path('export/teacher-load/', views.export_teacher_load, name='export_teacher_load'),
]
