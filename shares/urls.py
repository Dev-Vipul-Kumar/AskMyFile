from django.urls import path
from . import views

app_name = "shares"

urlpatterns = [
    path("create/",                  views.create_share_view, name="create"),
    path("upload/start/",            views.large_upload_start, name="large_upload_start"),
    path("upload/chunk/",            views.large_upload_chunk, name="large_upload_chunk"),
    path("upload/complete/",         views.large_upload_complete, name="large_upload_complete"),
    path("send-otp-email/",          views.send_otp_email,    name="send_otp_email"),
    path("access/",                  views.share_access,      name="access"),
    path("<int:share_id>/download/", views.share_download,    name="download"),
    path("<int:share_id>/revoke/",   views.revoke_share,      name="revoke"),
    path("list/",                    views.share_list,        name="list"),
    path("quota/",                   views.quota_status,      name="quota_status"),
]
