from django.urls import path

from . import views

urlpatterns = [
    path("", views.classificator_settings, name="settings"),

]