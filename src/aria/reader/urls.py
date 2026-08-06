from django.contrib.auth import views as auth_views
from django.urls import path

from aria.reader import views

app_name = "reader"

urlpatterns = [
    path("login/", views.ReaderLoginView.as_view(), name="login"),
    path(
        "logout/",
        auth_views.LogoutView.as_view(next_page="reader:login"),
        name="logout",
    ),
    path("assets/<str:asset_name>", views.reader_asset, name="asset"),
    path("", views.search, name="search"),
    path("documents/<uuid:identity_id>/", views.document_detail, name="document-detail"),
    path("artifacts/<uuid:artifact_id>/content/", views.artifact_content, name="artifact-content"),
]
