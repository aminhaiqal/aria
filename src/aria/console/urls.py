from django.contrib.auth import views as auth_views
from django.urls import path

from aria.console import views

app_name = "console"

urlpatterns = [
    path(
        "login/",
        views.StaffLoginView.as_view(),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("assets/<str:asset_name>", views.console_asset, name="asset"),
    path("", views.dashboard, name="dashboard"),
    path("sources/", views.source_list, name="source-list"),
    path("confidence/", views.source_confidence, name="source-confidence"),
    path("admissions/", views.admission_list, name="admission-list"),
    path(
        "admissions/<uuid:endpoint_id>/",
        views.admission_detail,
        name="admission-detail",
    ),
    path(
        "admissions/<uuid:endpoint_id>/capture/",
        views.admission_capture,
        name="admission-capture",
    ),
    path(
        "admissions/<uuid:endpoint_id>/assess/",
        views.admission_assess,
        name="admission-assess",
    ),
    path(
        "admissions/<uuid:endpoint_id>/promote/",
        views.admission_promote,
        name="admission-promote",
    ),
    path("sources/<uuid:endpoint_id>/", views.source_detail, name="source-detail"),
    path(
        "sources/<uuid:endpoint_id>/poll/",
        views.source_poll,
        name="source-poll",
    ),
    path(
        "sources/<uuid:endpoint_id>/pilot/",
        views.static_source_pilot,
        name="static-source-pilot",
    ),
    path(
        "sources/<uuid:endpoint_id>/assess/",
        views.static_source_assess,
        name="static-source-assess",
    ),
    path(
        "sources/<uuid:endpoint_id>/promote/",
        views.static_source_promote,
        name="static-source-promote",
    ),
    path("resources/<uuid:resource_id>/", views.resource_detail, name="resource-detail"),
    path(
        "resources/<uuid:resource_id>/poll/",
        views.resource_poll,
        name="resource-poll",
    ),
    path(
        "resources/<uuid:resource_id>/retire/",
        views.resource_retire,
        name="resource-retire",
    ),
    path("workflows/", views.orchestration_list, name="orchestration-list"),
    path(
        "workflows/<uuid:orchestration_id>/",
        views.orchestration_detail,
        name="orchestration-detail",
    ),
    path(
        "workflows/<uuid:orchestration_id>/retry/",
        views.orchestration_retry,
        name="orchestration-retry",
    ),
    path("comparisons/", views.comparison_list, name="comparison-list"),
    path(
        "comparisons/<uuid:comparison_id>/",
        views.comparison_detail,
        name="comparison-detail",
    ),
    path(
        "comparison-items/<uuid:item_id>/review/",
        views.comparison_item_review,
        name="comparison-item-review",
    ),
    path(
        "comparisons/<uuid:comparison_id>/summarize/",
        views.comparison_summarize,
        name="comparison-summarize",
    ),
    path(
        "comparisons/<uuid:comparison_id>/publish/",
        views.comparison_publish,
        name="comparison-publish",
    ),
    path("impacts/", views.impact_list, name="impact-list"),
    path("impacts/<uuid:impact_id>/", views.impact_detail, name="impact-detail"),
    path(
        "impacts/<uuid:impact_id>/review/",
        views.impact_review,
        name="impact-review",
    ),
    path(
        "impacts/<uuid:impact_id>/publish/",
        views.impact_publish,
        name="impact-publish",
    ),
    path("audit/", views.audit_list, name="audit-list"),
]
