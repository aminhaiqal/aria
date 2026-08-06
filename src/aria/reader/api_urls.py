from django.urls import path

from aria.reader.api import ReaderDocumentAPIView, ReaderOptionsAPIView, ReaderSearchAPIView

app_name = "reader-api"

urlpatterns = [
    path("options/", ReaderOptionsAPIView.as_view(), name="options"),
    path("search/", ReaderSearchAPIView.as_view(), name="search"),
    path(
        "documents/<uuid:identity_id>/",
        ReaderDocumentAPIView.as_view(),
        name="document-detail",
    ),
]
