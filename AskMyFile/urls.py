from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path("admin/",        admin.site.urls),
    path("",              include("core.urls")),
    path("accounts/",     include("accounts.urls")),
    path("shares/",       include("shares.urls")),
    path("billing/",      include("billing.urls")),
    path("admin-panel/",  include("admin_panel.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
