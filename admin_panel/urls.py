from django.urls import path
from . import views

app_name = "admin_panel"

urlpatterns = [
    path("",                              views.dashboard,           name="dashboard"),
    path("users/",                        views.user_list,           name="users"),
    path("users/create/",                 views.user_create,         name="user_create"),
    path("users/<int:user_id>/",          views.user_detail,         name="user_detail"),
    path("users/<int:user_id>/edit/",     views.user_edit,           name="user_edit"),
    path("users/<int:user_id>/delete/",   views.user_delete,         name="user_delete"),
    path("shares/",                       views.share_list,          name="shares"),
    path("shares/<int:share_id>/revoke/", views.revoke_share,        name="revoke_share"),
    path("shares/<int:share_id>/delete/", views.delete_share,        name="delete_share"),
    path("subscriptions/",                views.subscription_list,   name="subscriptions"),
    path("subscriptions/<int:sub_id>/",   views.update_subscription, name="update_subscription"),
    path("plans/",                        views.plans_manage,        name="plans"),
    path("quota/",                        views.quota_manage,        name="quota"),
    path("share/",                        views.admin_share_page,    name="admin_share"),
    path("api/stats/",                    views.stats_api,           name="stats_api"),
]
