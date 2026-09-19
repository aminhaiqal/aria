import hashlib
import uuid

from django.conf import settings

from aria.events.services import record_audit_event

SEARCH_COMPLETED = "reader.search.completed"
DOCUMENT_VIEWED = "reader.document.viewed"
EVIDENCE_DOWNLOADED = "reader.evidence.downloaded"
PILOT_FEEDBACK_RECORDED = "product.pilot_feedback.recorded"
CHAT_ANSWERED = "reader.chat.answered"


def _user_target_id(user) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"aria:reader-user:{user.pk}")


def _record(*, action: str, user, target_type: str, target_id, details: dict) -> None:
    if not settings.READER_USAGE_TRACKING or not user.is_authenticated or not user.is_active:
        return
    record_audit_event(
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_type="user",
        actor_identifier=str(user.pk),
        details=details,
    )


def record_reader_search(*, user, query: str, result: dict) -> None:
    normalized_query = query.strip()
    filters = result.get("filters", {})
    _record(
        action=SEARCH_COMPLETED,
        user=user,
        target_type="reader_user",
        target_id=_user_target_id(user),
        details={
            "query_sha256": hashlib.sha256(normalized_query.encode("utf-8")).hexdigest(),
            "query_length": len(normalized_query),
            "mode": result.get("mode", ""),
            "embedding_provider": (result.get("embedding") or {}).get("provider", ""),
            "profile_id": filters.get("profile", ""),
            "authority": filters.get("authority", ""),
            "collection": str(filters.get("collection", "") or ""),
            "page": result.get("page", 1),
            "page_size": result.get("page_size", 0),
            "result_count": len(result.get("results", [])),
            "bounded_result_count": result.get("bounded_result_count", 0),
            "warning_count": len(result.get("warnings", [])),
        },
    )


def record_reader_document_view(*, user, identity_id, profile_id: str = "") -> None:
    _record(
        action=DOCUMENT_VIEWED,
        user=user,
        target_type="document_identity",
        target_id=identity_id,
        details={"profile_id": profile_id},
    )


def record_reader_evidence_download(*, user, artifact) -> None:
    _record(
        action=EVIDENCE_DOWNLOADED,
        user=user,
        target_type="raw_artifact",
        target_id=artifact.id,
        details={
            "artifact_sha256": artifact.sha256,
            "content_type": artifact.detected_content_type,
            "byte_size": artifact.byte_size,
        },
    )


def record_reader_chat_answer(*, user, thread, question: str, assistant_message) -> None:
    normalized_question = question.strip()
    _record(
        action=CHAT_ANSWERED,
        user=user,
        target_type="reader_chat_thread",
        target_id=thread.id,
        details={
            "question_sha256": hashlib.sha256(
                normalized_question.encode("utf-8")
            ).hexdigest(),
            "question_length": len(normalized_question),
            "scope": "document" if thread.document_identity_id else "corpus",
            "document_identity_id": str(thread.document_identity_id or ""),
            "document_version_id": str(thread.document_version_id or ""),
            "provider": assistant_message.provider,
            "model": assistant_message.model,
            "citation_count": assistant_message.citations.count(),
            "input_tokens": assistant_message.input_tokens,
            "output_tokens": assistant_message.output_tokens,
        },
    )


def record_pilot_feedback(
    *,
    user,
    category: str,
    rating: int,
    comment: str,
    recorded_by: str,
) -> None:
    record_audit_event(
        action=PILOT_FEEDBACK_RECORDED,
        target_type="reader_user",
        target_id=_user_target_id(user),
        actor_type="pilot_user",
        actor_identifier=str(user.pk),
        details={
            "username": user.get_username(),
            "category": category,
            "rating": rating,
            "comment": comment,
            "recorded_by": recorded_by,
        },
    )
