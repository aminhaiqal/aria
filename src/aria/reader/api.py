from uuid import UUID

from django.http import Http404
from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from aria.documents.models import DocumentIdentity
from aria.reader.services import (
    ReaderQueryError,
    ReaderSearchUnavailable,
    reader_document_payload,
    search_reader_documents,
)


class IsActiveAuthenticated(BasePermission):
    def has_permission(self, request, view) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_active)


class ReaderSearchThrottle(UserRateThrottle):
    scope = "reader_search"


class ReaderDocumentThrottle(UserRateThrottle):
    scope = "reader_document"


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
            result = search_reader_documents(
                request.query_params.get("q", ""),
                mode=request.query_params.get("mode", "hybrid"),
                provider_name=request.query_params.get("embedding_provider", ""),
                authority_slug=request.query_params.get("authority", ""),
                collection_id=request.query_params.get("collection", ""),
                date_from=_date_parameter(request.query_params.get("date_from", ""), "date_from"),
                date_to=_date_parameter(request.query_params.get("date_to", ""), "date_to"),
                page=_positive_integer(
                    request.query_params.get("page", ""),
                    name="page",
                    default=1,
                ),
                page_size=_positive_integer(
                    request.query_params.get("page_size", ""),
                    name="page_size",
                    default=10,
                ),
            )
        except ReaderQueryError as error:
            return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)
        except ReaderSearchUnavailable as error:
            return Response({"detail": str(error)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
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
            payload = reader_document_payload(identity_id)
        except DocumentIdentity.DoesNotExist as error:
            raise Http404 from error
        return Response(payload)
