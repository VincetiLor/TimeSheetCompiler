from django.urls import path

from . import views

# Inclusa senza prefisso in mysite/urls.py: la home sta su /index/, mentre il
# redirect verso Google resta sotto /auth/.
urlpatterns = [
    path("index/", views.login_view, name="index"),
    path("auth/avvia-login-google/", views.avvia_login_google, name="avvia_login_google"),
]
