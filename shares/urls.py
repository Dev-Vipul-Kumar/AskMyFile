from django.urls import path
from . import views

app_name = "shares"

urlpatterns = [
    path("create/",              views.create_share_view, name="create"),
    path("access/",              views.share_access,      name="access"),
    path("<int:share_id>/download/", views.share_download, name="download"),
    path("<int:share_id>/revoke/",   views.revoke_share,   name="revoke"),
    path("list/",                views.share_list,        name="list"),
    path("quota/",               views.quota_status,      name="quota_status"),
]
