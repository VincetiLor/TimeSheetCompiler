from django.urls import path

from . import views

urlpatterns = [
    path("", views.Hook_settings.as_view(), name="settings"),
]