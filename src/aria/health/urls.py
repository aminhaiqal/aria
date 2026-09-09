from django.urls import path

from aria.health.views import liveness, metrics, readiness

app_name = "health"

urlpatterns = [
    path("live/", liveness, name="live"),
    path("ready/", readiness, name="ready"),
    path("metrics/", metrics, name="metrics"),
]
