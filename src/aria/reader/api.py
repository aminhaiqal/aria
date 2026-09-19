from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from aria.collections.models import PublicationCollection
from aria.documents.models import DocumentIdentity
from aria.impacts.models import ApplicabilityTaxonomy, ApplicabilityTerm, BusinessProfile
from aria.impacts.profiles import evaluate_current_impacts, save_business_profile
from aria.knowledge.embedding_services import current_versions_queryset
from aria.reader.chat import (
    ReaderChatError,
    ReaderChatUnavailable,
    answer_chat_question,
    thread_payload,
)
from aria.reader.models import ReaderChatThread
from aria.reader.services import (
    ReaderQueryError,
    ReaderSearchUnavailable,
    browse_reader_documents,
    reader_document_payload,
    search_reader_documents,
)
from aria.reader.usage import (
    record_reader_chat_answer,
    record_reader_document_view,
    record_reader_search,
)


class IsActiveAuthenticated(BasePermission):
    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_active)


class ReaderSearchThrottle(UserRateThrottle):
    scope = "reader_search"


class ReaderDocumentThrottle(UserRateThrottle):
    scope = "reader_document"


class ReaderChatThrottle(UserRateThrottle):
    scope = "reader_chat"


def _profile_payload(profile: BusinessProfile) -> dict:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "notes": profile.notes,
        "taxonomy_id": str(profile.taxonomy_id),
        "is_active": profile.is_active,
        "term_ids": [str(term_id) for term_id in profile.terms.values_list("id", flat=True)],
        "updated_at": profile.updated_at,
    }


def _owned_profile(request, value: str) -> BusinessProfile | None:
    if not value:
        return None
    try:
        profile_id = UUID(value)
    except ValueError as error:
        raise Http404 from error
    try:
        return BusinessProfile.objects.select_related("taxonomy").get(
            pk=profile_id,
            owner=request.user,
            is_active=True,
        )
    except BusinessProfile.DoesNotExist as error:
        raise Http404 from error


def _latest_taxonomy() -> ApplicabilityTaxonomy | None:
    return ApplicabilityTaxonomy.objects.order_by("-applied_at", "slug", "-version").first()


class ReaderOptionsAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderDocumentThrottle,)

    def get(self, request):
        collections = list(
            PublicationCollection.objects.filter(
                is_enabled=True,
                is_evidence_eligible=True,
                authority__is_enabled=True,
            )
            .select_related("authority")
            .order_by("authority__name", "name", "id")
        )
        authorities = []
        seen_authorities = set()
        for collection in collections:
            authority = collection.authority
            if authority.id in seen_authorities:
                continue
            seen_authorities.add(authority.id)
            authorities.append(
                {
                    "id": str(authority.id),
                    "name": authority.name,
                    "slug": authority.slug,
                    "trust_classification": authority.trust_classification,
                }
            )
        taxonomy = _latest_taxonomy()
        profiles = BusinessProfile.objects.filter(
            owner=request.user,
            is_active=True,
        ).prefetch_related("terms")
        return Response(
            {
                "authorities": authorities,
                "collections": [
                    {
                        "id": str(collection.id),
                        "name": collection.name,
                        "document_family": collection.document_family,
                        "authority_slug": collection.authority.slug,
                    }
                    for collection in collections
                ],
                "applicability_taxonomy": (
                    {
                        "id": str(taxonomy.id),
                        "name": taxonomy.name,
                        "version": taxonomy.version,
                        "disclaimer": taxonomy.disclaimer,
                        "terms": [
                            {
                                "id": str(term.id),
                                "dimension": term.dimension,
                                "code": term.code,
                                "label": term.label,
                                "description": term.description,
                            }
                            for term in taxonomy.terms.order_by("dimension", "label")
                        ],
                    }
                    if taxonomy
                    else None
                ),
                "business_profiles": [_profile_payload(profile) for profile in profiles],
            }
        )


def _positive_integer(value: str, *, name: str, default: int) -> int:
    if not value:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ReaderQueryError(f"{name} must be an integer.") from error


class ReaderSearchAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderSearchThrottle,)

    def get(self, request):
        try:
            business_profile = _owned_profile(request, request.query_params.get("profile", ""))
            common_arguments = {
                "authority_slug": request.query_params.get("authority", ""),
                "collection_id": request.query_params.get("collection", ""),
                "date_from": _date_parameter(
                    request.query_params.get("date_from", ""), "date_from"
                ),
                "date_to": _date_parameter(request.query_params.get("date_to", ""), "date_to"),
                "page": _positive_integer(
                    request.query_params.get("page", ""),
                    name="page",
                    default=1,
                ),
                "page_size": _positive_integer(
                    request.query_params.get("page_size", ""),
                    name="page_size",
                    default=10,
                ),
                "business_profile": business_profile,
            }
            query = request.query_params.get("q", "").strip()
            if query:
                result = search_reader_documents(
                    query,
                    mode=request.query_params.get("mode", "hybrid"),
                    provider_name=request.query_params.get("embedding_provider", ""),
                    **common_arguments,
                )
            else:
                result = browse_reader_documents(**common_arguments)
        except ReaderQueryError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except ReaderSearchUnavailable as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        record_reader_search(
            user=request.user,
            query=request.query_params.get("q", ""),
            result=result,
        )
        return Response(result)


def _date_parameter(value: str, name: str):
    if not value:
        return None
    from datetime import date

    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ReaderQueryError(f"{name} must use YYYY-MM-DD.") from error


class ReaderDocumentAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderDocumentThrottle,)

    def get(self, request, identity_id: UUID):
        try:
            business_profile = _owned_profile(request, request.query_params.get("profile", ""))
            payload = reader_document_payload(
                identity_id,
                business_profile=business_profile,
            )
        except DocumentIdentity.DoesNotExist as error:
            raise Http404 from error
        record_reader_document_view(
            user=request.user,
            identity_id=identity_id,
            profile_id=str(business_profile.id) if business_profile else "",
        )
        return Response(payload)


def _reader_eligible_identity(identity_id: UUID) -> tuple[DocumentIdentity, object]:
    try:
        identity = DocumentIdentity.objects.select_related("collection__authority").get(
            pk=identity_id,
            superseded_by__isnull=True,
            collection__is_enabled=True,
            collection__is_evidence_eligible=True,
            collection__authority__is_enabled=True,
        )
    except DocumentIdentity.DoesNotExist as error:
        raise Http404 from error
    version = current_versions_queryset().filter(identity_id=identity.id).first()
    if version is None:
        raise Http404
    return identity, version


def _owned_thread(request, thread_id: UUID) -> ReaderChatThread:
    try:
        return ReaderChatThread.objects.select_related(
            "document_identity",
            "document_version",
        ).get(pk=thread_id, owner=request.user)
    except ReaderChatThread.DoesNotExist as error:
        raise Http404 from error


class ReaderChatThreadListAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderDocumentThrottle,)

    def get(self, request):
        document_value = request.query_params.get("document", "").strip()
        threads = ReaderChatThread.objects.filter(
            owner=request.user,
            status=ReaderChatThread.Status.ACTIVE,
        ).select_related("document_identity", "document_version")
        if document_value:
            try:
                document_id = UUID(document_value)
            except ValueError:
                return Response(
                    {"detail": "document must be a UUID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            threads = threads.filter(document_identity_id=document_id)
        else:
            threads = threads.filter(document_identity__isnull=True)
        threads = threads.annotate(message_count=Count("messages"))[:50]
        return Response({"results": [thread_payload(thread) for thread in threads]})

    def post(self, request):
        document_value = str(request.data.get("document_id", "")).strip()
        identity = None
        version = None
        if document_value:
            try:
                identity_id = UUID(document_value)
            except ValueError:
                return Response(
                    {"detail": "document_id must be a UUID."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            identity, version = _reader_eligible_identity(identity_id)
        title = request.data.get("title", "")
        if not isinstance(title, str) or len(title.strip()) > 160:
            return Response(
                {"detail": "title must be text no longer than 160 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        thread = ReaderChatThread.objects.create(
            owner=request.user,
            document_identity=identity,
            document_version=version,
            title=title.strip(),
        )
        return Response(
            thread_payload(thread, include_messages=True),
            status=status.HTTP_201_CREATED,
        )


class ReaderChatThreadDetailAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderDocumentThrottle,)

    def get(self, request, thread_id: UUID):
        thread = _owned_thread(request, thread_id)
        return Response(thread_payload(thread, include_messages=True))

    def patch(self, request, thread_id: UUID):
        thread = _owned_thread(request, thread_id)
        update_fields = ["updated_at"]
        if "title" in request.data:
            title = request.data["title"]
            if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
                return Response(
                    {"detail": "title must be between 1 and 160 characters."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            thread.title = title.strip()
            update_fields.append("title")
        if "status" in request.data:
            selected_status = request.data["status"]
            if selected_status not in ReaderChatThread.Status.values:
                return Response(
                    {"detail": "status must be active or archived."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            thread.status = selected_status
            update_fields.append("status")
        thread.save(update_fields=tuple(update_fields))
        return Response(thread_payload(thread, include_messages=True))


class ReaderChatMessageAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderChatThrottle,)

    def post(self, request, thread_id: UUID):
        thread = _owned_thread(request, thread_id)
        question = request.data.get("question", "")
        if not isinstance(question, str):
            return Response(
                {"detail": "question must be text."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            _, assistant_message = answer_chat_question(thread, question)
        except ReaderChatError as error:
            response_status = (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if isinstance(error, ReaderChatUnavailable)
                else status.HTTP_400_BAD_REQUEST
            )
            return Response({"detail": str(error)}, status=response_status)
        thread.refresh_from_db()
        record_reader_chat_answer(
            user=request.user,
            thread=thread,
            question=question,
            assistant_message=assistant_message,
        )
        return Response(
            thread_payload(thread, include_messages=True),
            status=status.HTTP_201_CREATED,
        )


def _save_profile_from_request(request, profile: BusinessProfile | None = None):
    raw_term_ids = request.data.get("term_ids", [])
    if not isinstance(raw_term_ids, list) or len(raw_term_ids) > 50:
        raise ValidationError("term_ids must be a list of no more than 50 identifiers.")
    try:
        term_ids = [UUID(str(value)) for value in raw_term_ids]
    except ValueError as error:
        raise ValidationError("Every term identifier must be a UUID.") from error
    if len(term_ids) != len(set(term_ids)):
        raise ValidationError("term_ids cannot contain duplicates.")
    if profile is not None:
        taxonomy = profile.taxonomy
    else:
        try:
            taxonomy_id = UUID(str(request.data.get("taxonomy_id", "")))
            taxonomy = ApplicabilityTaxonomy.objects.get(pk=taxonomy_id)
        except (ValueError, ApplicabilityTaxonomy.DoesNotExist) as error:
            raise ValidationError("Select an installed applicability taxonomy.") from error
    terms = list(ApplicabilityTerm.objects.filter(id__in=term_ids))
    if len(terms) != len(term_ids):
        raise ValidationError("One or more selected applicability terms do not exist.")
    name = request.data.get("name", "")
    notes = request.data.get("notes", "")
    if not isinstance(name, str) or not isinstance(notes, str):
        raise ValidationError("Profile name and notes must be text.")
    saved = save_business_profile(
        profile=profile,
        owner=request.user,
        taxonomy=taxonomy,
        name=name,
        notes=notes,
        terms=terms,
    )
    return saved, evaluate_current_impacts(saved)


def _validation_response(error: ValidationError) -> Response:
    return Response(
        {"detail": "; ".join(error.messages)},
        status=status.HTTP_400_BAD_REQUEST,
    )


class ReaderProfileListAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderDocumentThrottle,)

    def get(self, request):
        profiles = BusinessProfile.objects.filter(owner=request.user).prefetch_related("terms")
        return Response({"results": [_profile_payload(profile) for profile in profiles]})

    def post(self, request):
        try:
            profile, evaluation = _save_profile_from_request(request)
        except ValidationError as error:
            return _validation_response(error)
        return Response(
            {"profile": _profile_payload(profile), "evaluation": evaluation},
            status=status.HTTP_201_CREATED,
        )


class ReaderProfileDetailAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderDocumentThrottle,)

    def put(self, request, profile_id: UUID):
        try:
            profile = BusinessProfile.objects.select_related("taxonomy").get(
                pk=profile_id,
                owner=request.user,
            )
        except BusinessProfile.DoesNotExist as error:
            raise Http404 from error
        try:
            profile, evaluation = _save_profile_from_request(request, profile)
        except ValidationError as error:
            return _validation_response(error)
        return Response({"profile": _profile_payload(profile), "evaluation": evaluation})


class ReaderProfileEvaluateAPIView(APIView):
    permission_classes = (IsActiveAuthenticated,)
    throttle_classes = (ReaderDocumentThrottle,)

    def post(self, request, profile_id: UUID):
        profile = _owned_profile(request, str(profile_id))
        return Response(
            {
                "profile": _profile_payload(profile),
                "evaluation": evaluate_current_impacts(profile),
            }
        )
