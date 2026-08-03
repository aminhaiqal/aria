from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("aria.health.urls")),
    path("api/v1/", include("aria.api.urls")),
]
