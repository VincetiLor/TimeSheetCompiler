"""
URL configuration for mysite project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('hooks/', include('hooks.urls')),
    # Senza prefisso: /index/ (home) e /auth/avvia-login-google/.
    path('', include('authentication.urls')),
    path('accounts/', include('allauth.urls')),
    # Senza prefisso: classificationpipeline.urls porta i propri
    # (/calendars/ e /events/), vedi il commento in quel file.
    path('', include('classificationpipeline.urls')),
    path('admin/', admin.site.urls),
]
