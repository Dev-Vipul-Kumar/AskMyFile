from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("register/",                   views.register,                  name="register"),
    path("verify-email/<str:token>/",   views.verify_email,              name="verify_email"),
    path("resend-email/",               views.resend_email_verification, name="resend_email_verification"),
    path("login/",                      views.user_login,                name="login"),
    path("logout/",                     views.user_logout,               name="logout"),
    path("password-reset/",             views.password_reset_request,    name="password_reset_request"),
    path("password-reset/<str:token>/", views.password_reset_confirm,    name="password_reset_confirm"),
]
