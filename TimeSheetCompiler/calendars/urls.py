from django.urls import path

from . import views

app_name = 'calendars'

urlpatterns = [
    path("", views.SelectCalendarsView.as_view(), name="eventi_calendars"),
]