import hashlib
from io import BytesIO
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from aria.artifacts.models import RawArtifact
from aria.artifacts.storage import get_artifact_store
from aria.documents.models import DocumentIdentity
from aria.reader.forms import ReaderSearchForm
from aria.reader.services import (
    ReaderQueryError,
    ReaderSearchUnavailable,
    reader_document_payload,
    search_reader_documents,
)

reader_required = login_required(login_url="reader:login")

ASSETS = {
    "reader.css": ("text/css; charset=utf-8", "reader.css"),
}


class ReaderLoginView(LoginView):
    template_name = "reader/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse("reader:search")


@require_GET
def reader_asset(request, asset_name):
    asset = ASSETS.get(asset_name)
    if asset is None:
        raise Http404
    content_type, filename = asset
    path = Path(__file__).with_name("static") / filename
    response = FileResponse(path.open("rb"), content_type=content_type)
    response["Cache-Control"] = "public, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _page_query(request, page: int) -> str:
    query = request.GET.copy()
    query["page"] = str(page)
    return urlencode(query, doseq=True)


@reader_required
@require_GET
def search(request):
    has_query = bool(request.GET.get("q", "").strip())
    form = ReaderSearchForm(request.GET if has_query else None)
    result = None
    if has_query and form.is_valid():
        try:
            page = int(request.GET.get("page", "1"))
        except ValueError:
            page = 0
        try:
            result = search_reader_documents(
                form.cleaned_data["q"],
                mode=form.cleaned_data.get("mode") or "hybrid",
                provider_name=form.cleaned_data.get("embedding_provider") or "",
                authority_slug=form.cleaned_data.get("authority") or "",
                collection_id=form.cleaned_data.get("collection") or "",
                date_from=form.cleaned_data.get("date_from"),
                date_to=form.cleaned_data.get("date_to"),
                page=page,
                page_size=settings.READER_PAGE_SIZE,
            )
        except ReaderQueryError as error:
            form.add_error(None, str(error))
        except ReaderSearchUnavailable as error:
            form.add_error(None, str(error))

    context = {
        "page_title": "Search official material",
        "form": form,
        "has_query": has_query,
        "result": result,
        "previous_query": (
            _page_query(request, result["page"] - 1)
            if result and result["has_previous"]
            else ""
        ),
        "next_query": (
            _page_query(request, result["page"] + 1) if result and result["has_next"] else ""
        ),
    }
    return render(request, "reader/search.html", context)


@reader_required
@require_GET
def document_detail(request, identity_id: UUID):
    try:
        document = reader_document_payload(identity_id)
    except DocumentIdentity.DoesNotExist as error:
        raise Http404 from error
    return render(
        request,
        "reader/document_detail.html",
        {
            "page_title": document["identity"]["title"],
            "document": document,
        },
    )


@reader_required
@require_GET
def artifact_content(request, artifact_id: UUID):
    readable = RawArtifact.objects.filter(
        Q(
            document_version_evidence__document_version__identity__collection__is_enabled=True,
            document_version_evidence__document_version__identity__collection__is_evidence_eligible=True,
            document_version_evidence__document_version__identity__collection__authority__is_enabled=True,
        )
        | Q(
            normalized_sections__document_version__identity__collection__is_enabled=True,
            normalized_sections__document_version__identity__collection__is_evidence_eligible=True,
            normalized_sections__document_version__identity__collection__authority__is_enabled=True,
        )
    ).distinct()
    artifact = get_object_or_404(readable, pk=artifact_id)
    store = get_artifact_store(artifact.storage_backend)
    content = store.read(artifact.storage_key)
    if len(content) != artifact.byte_size or hashlib.sha256(content).hexdigest() != artifact.sha256:
        return HttpResponse(
            "Archived evidence failed integrity verification.",
            status=409,
            content_type="text/plain; charset=utf-8",
        )
    suffix = {
        "application/pdf": ".pdf",
        "text/html": ".html",
        "text/plain": ".txt",
    }.get(artifact.detected_content_type, ".bin")
    response = FileResponse(
        BytesIO(content),
        content_type=artifact.detected_content_type,
        as_attachment=True,
        filename=f"aria-evidence-{artifact.sha256}{suffix}",
    )
    response["X-Content-SHA256"] = artifact.sha256
    response["X-ARIA-Evidence"] = "immutable-artifact"
    return response
