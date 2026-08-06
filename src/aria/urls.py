from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="reader:search", permanent=False)),
    path("reader/", include("aria.reader.urls")),
    path("console/", include("aria.console.urls")),
    path("admin/", admin.site.urls),
    path("health/", include("aria.health.urls")),
    path("api/v1/", include("aria.api.urls")),
    path("api/reader/v1/", include("aria.reader.api_urls")),
]
