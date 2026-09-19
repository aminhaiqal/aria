from django.conf import settings
from django.db import models
from django.utils import timezone

from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.documents.models import DocumentIdentity, DocumentVersion, NormalizedSection


class ReaderChatThread(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reader_chat_threads",
    )
    document_identity = models.ForeignKey(
        DocumentIdentity,
        on_delete=models.PROTECT,
        related_name="reader_chat_threads",
        null=True,
        blank=True,
    )
    document_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="reader_chat_threads",
        null=True,
        blank=True,
    )
    title = models.CharField(max_length=160, blank=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-last_message_at", "-created_at")
        indexes = [
            models.Index(fields=("owner", "status", "last_message_at")),
            models.Index(fields=("owner", "document_identity", "status")),
        ]

    def __str__(self) -> str:
        return self.title or f"ARIA conversation {self.id}"


class ReaderChatMessage(AppendOnlyModel):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    thread = models.ForeignKey(
        ReaderChatThread,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    provider = models.CharField(max_length=64, blank=True)
    model = models.CharField(max_length=128, blank=True)
    provider_response_id = models.CharField(max_length=255, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    suggested_questions = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("created_at", "id")
        indexes = [models.Index(fields=("thread", "created_at"))]

    def __str__(self) -> str:
        return f"{self.thread_id}:{self.role}:{self.created_at.isoformat()}"


class ReaderChatCitation(AppendOnlyModel):
    message = models.ForeignKey(
        ReaderChatMessage,
        on_delete=models.CASCADE,
        related_name="citations",
    )
    normalized_section = models.ForeignKey(
        NormalizedSection,
        on_delete=models.PROTECT,
        related_name="reader_chat_citations",
    )
    ordinal = models.PositiveIntegerField()
    excerpt = models.TextField()
    section_text_sha256 = models.CharField(max_length=64)
    artifact_sha256 = models.CharField(max_length=64)
    document_version_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("ordinal", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("message", "normalized_section"),
                name="unique_reader_chat_message_section_citation",
            )
        ]

    def __str__(self) -> str:
        return f"{self.message_id} cites {self.normalized_section_id}"
