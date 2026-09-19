from django.urls import path

from aria.reader.api import (
    ReaderChatMessageAPIView,
    ReaderChatThreadDetailAPIView,
    ReaderChatThreadListAPIView,
    ReaderDocumentAPIView,
    ReaderOptionsAPIView,
    ReaderProfileDetailAPIView,
    ReaderProfileEvaluateAPIView,
    ReaderProfileListAPIView,
    ReaderSearchAPIView,
)

app_name = "reader-api"

urlpatterns = [
    path("chat/threads/", ReaderChatThreadListAPIView.as_view(), name="chat-thread-list"),
    path(
        "chat/threads/<uuid:thread_id>/",
        ReaderChatThreadDetailAPIView.as_view(),
        name="chat-thread-detail",
    ),
    path(
        "chat/threads/<uuid:thread_id>/messages/",
        ReaderChatMessageAPIView.as_view(),
        name="chat-message-list",
    ),
    path("options/", ReaderOptionsAPIView.as_view(), name="options"),
    path("search/", ReaderSearchAPIView.as_view(), name="search"),
    path("profiles/", ReaderProfileListAPIView.as_view(), name="profile-list"),
    path(
        "profiles/<uuid:profile_id>/",
        ReaderProfileDetailAPIView.as_view(),
        name="profile-detail",
    ),
    path(
        "profiles/<uuid:profile_id>/evaluate/",
        ReaderProfileEvaluateAPIView.as_view(),
        name="profile-evaluate",
    ),
    path(
        "documents/<uuid:identity_id>/",
        ReaderDocumentAPIView.as_view(),
        name="document-detail",
    ),
]
