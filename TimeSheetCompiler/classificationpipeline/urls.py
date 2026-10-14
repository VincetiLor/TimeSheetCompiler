from django.urls import path

from . import views


app_name = 'classificationpipeline'

# Every page works on one timesheet; the working range travels in the query
# string (?start=YYYY-MM-DD&end=YYYY-MM-DD).
urlpatterns = [
    path('timesheets/', views.timesheet_list, name='timesheet_list'),
    path('timesheets/new/', views.timesheet_create, name='timesheet_create'),
    path('timesheets/<int:timesheet_id>/settings/', views.timesheet_settings, name='timesheet_settings'),
    path('timesheets/<int:timesheet_id>/close/', views.timesheet_close, name='timesheet_close'),
    path('timesheets/<int:timesheet_id>/delete/', views.timesheet_delete, name='timesheet_delete'),
    path('timesheets/<int:timesheet_id>/calendars/', views.SelectCalendarsView.as_view(), name='eventi_calendars'),
    path('timesheets/<int:timesheet_id>/events/', views.event_compare, name='event_compare'),
    path('timesheets/<int:timesheet_id>/events/<int:event_id>/delete/', views.event_delete, name='event_delete'),
    path('timesheets/<int:timesheet_id>/events/classifica/', views.classifica_incerti, name='classifica_incerti'),
    path('timesheets/<int:timesheet_id>/events/classifica-ai/', views.classifica_incerti_ai, name='classifica_incerti_ai'),
    path('timesheets/<int:timesheet_id>/events/compile/', views.timesheet_compile, name='timesheet_compile'),
]
