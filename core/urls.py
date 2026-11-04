from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("app/", views.home, name="home"),
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("help/", views.help_page, name="help"),
    path("health/", views.health, name="health"),
]
