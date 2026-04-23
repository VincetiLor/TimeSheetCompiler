from django.urls import path

from . import views

urlpatterns = [
    path("", views.SelectCalendarsView.as_view(), name="eventi_calendars"),
    path("compare/", views.event_compare, name="event_compare"),
]