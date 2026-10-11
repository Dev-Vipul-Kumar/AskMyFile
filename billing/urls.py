from django.urls import path
from . import views

app_name = "billing"

urlpatterns = [
    path("plans/",            views.plans_view,        name="plans"),
    path("create-order/",     views.create_order,      name="create_order"),
    path("verify/",           views.verify_payment,    name="verify_payment"),
    path("webhook/",          views.webhook_view,      name="webhook"),
    path("payment/success/",  views.payment_success,   name="payment_success"),
    path("payment/failure/",  views.payment_failure,   name="payment_failure"),
    path("status/",           views.subscription_status, name="subscription_status"),
]
