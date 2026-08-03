from io import BytesIO

from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.http import FileResponse
from pgvector.django import CosineDistance
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from aria.api.serializers import (
    ArtifactDerivativeSerializer,
    ArtifactObservationSerializer,
    AuthoritySerializer,
    DiscoveredCandidateSerializer,
    DocumentIdentitySerializer,
    DocumentQualityAssessmentSerializer,
    DocumentVersionSerializer,
    ExtractedDocumentSerializer,
    ExtractionRunSerializer,
    FetchAttemptSerializer,
    GraphEdgeSerializer,
    GraphNodeSerializer,
    NormalizedSectionSerializer,
    OCRRunSerializer,
    PublicationCollectionSerializer,
    QualityAssessmentRunSerializer,
    QualityFindingSerializer,
    RawArtifactSerializer,
    SourceEndpointSerializer,
    SourceRunSerializer,
)
from aria.artifacts.models import ArtifactDerivative, ArtifactObservation, RawArtifact
from aria.artifacts.storage import get_artifact_store
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.documents.models import DocumentIdentity, DocumentVersion, NormalizedSection
from aria.extraction.models import ExtractedDocument, ExtractionRun
from aria.fetching.models import FetchAttempt
from aria.knowledge.embeddings import embed_text, embedding_configuration
from aria.knowledge.models import GraphEdge, GraphNode
from aria.ocr.models import OCRRun
from aria.quality.models import (
    DocumentQualityAssessment,
    QualityAssessmentRun,
    QualityFinding,
)
from aria.sources.models import SourceEndpoint


class AuthorityViewSet(ReadOnlyModelViewSet):
    queryset = Authority.objects.all()
    serializer_class = AuthoritySerializer
    filterset_fields = ()


class PublicationCollectionViewSet(ReadOnlyModelViewSet):
    queryset = PublicationCollection.objects.select_related("authority")
    serializer_class = PublicationCollectionSerializer


class SourceEndpointViewSet(ReadOnlyModelViewSet):
    queryset = SourceEndpoint.objects.select_related(
        "collection", "collection__authority"
    ).prefetch_related("connector_configurations")
    serializer_class = SourceEndpointSerializer


class SourceRunViewSet(ReadOnlyModelViewSet):
    queryset = SourceRun.objects.select_related("endpoint")
    serializer_class = SourceRunSerializer


class DiscoveredCandidateViewSet(ReadOnlyModelViewSet):
    queryset = DiscoveredCandidate.objects.select_related("endpoint", "latest_source_run")
    serializer_class = DiscoveredCandidateSerializer


class FetchAttemptViewSet(ReadOnlyModelViewSet):
    queryset = FetchAttempt.objects.select_related("candidate", "source_run")
    serializer_class = FetchAttemptSerializer


class RawArtifactViewSet(ReadOnlyModelViewSet):
    queryset = RawArtifact.objects.prefetch_related("observations")
    serializer_class = RawArtifactSerializer

    @action(detail=True, methods=("get",))
    def content(self, request, pk=None):
        artifact = self.get_object()
        store = get_artifact_store(artifact.storage_backend)
        content = store.read(artifact.storage_key)
        response = FileResponse(
            BytesIO(content),
            content_type=artifact.detected_content_type,
            as_attachment=True,
            filename=artifact.sha256,
        )
        response["X-Content-SHA256"] = artifact.sha256
        return response


class ArtifactObservationViewSet(ReadOnlyModelViewSet):
    queryset = ArtifactObservation.objects.select_related(
        "raw_artifact",
        "fetch_attempt",
        "candidate",
        "source_run",
    )
    serializer_class = ArtifactObservationSerializer


class ArtifactDerivativeViewSet(ReadOnlyModelViewSet):
    queryset = ArtifactDerivative.objects.select_related("source_artifact", "derived_artifact")
    serializer_class = ArtifactDerivativeSerializer


class ExtractionRunViewSet(ReadOnlyModelViewSet):
    queryset = ExtractionRun.objects.select_related("raw_artifact")
    serializer_class = ExtractionRunSerializer


class ExtractedDocumentViewSet(ReadOnlyModelViewSet):
    queryset = ExtractedDocument.objects.select_related(
        "extraction_run", "raw_artifact"
    ).prefetch_related("blocks")
    serializer_class = ExtractedDocumentSerializer


class OCRRunViewSet(ReadOnlyModelViewSet):
    queryset = OCRRun.objects.select_related(
        "source_artifact",
        "searchable_pdf_derivative",
        "searchable_pdf_derivative__derived_artifact",
        "text_sidecar_derivative",
        "text_sidecar_derivative__derived_artifact",
    )
    serializer_class = OCRRunSerializer


class DocumentIdentityViewSet(ReadOnlyModelViewSet):
    queryset = DocumentIdentity.objects.select_related(
        "collection", "collection__authority", "superseded_by"
    ).prefetch_related("versions")
    serializer_class = DocumentIdentitySerializer


class DocumentVersionViewSet(ReadOnlyModelViewSet):
    queryset = DocumentVersion.objects.select_related("identity").prefetch_related(
        "sections", "evidence_records"
    )
    serializer_class = DocumentVersionSerializer


class NormalizedSectionViewSet(ReadOnlyModelViewSet):
    queryset = NormalizedSection.objects.select_related(
        "document_version", "source_artifact", "extraction_run"
    )
    serializer_class = NormalizedSectionSerializer


class GraphNodeViewSet(ReadOnlyModelViewSet):
    queryset = GraphNode.objects.all()
    serializer_class = GraphNodeSerializer

    @action(detail=True, methods=("get",))
    def neighbors(self, request, pk=None):
        node = self.get_object()
        outgoing = node.outgoing_edges.select_related("subject", "object")
        incoming = node.incoming_edges.select_related("subject", "object")
        return Response(
            {
                "node": self.get_serializer(node).data,
                "outgoing": GraphEdgeSerializer(outgoing, many=True).data,
                "incoming": GraphEdgeSerializer(incoming, many=True).data,
            }
        )


class GraphEdgeViewSet(ReadOnlyModelViewSet):
    queryset = GraphEdge.objects.select_related("subject", "object")
    serializer_class = GraphEdgeSerializer


class QualityAssessmentRunViewSet(ReadOnlyModelViewSet):
    queryset = QualityAssessmentRun.objects.select_related("collection", "collection__authority")
    serializer_class = QualityAssessmentRunSerializer


class DocumentQualityAssessmentViewSet(ReadOnlyModelViewSet):
    queryset = DocumentQualityAssessment.objects.select_related(
        "quality_run", "document_version", "source_artifact"
    ).prefetch_related("findings")
    serializer_class = DocumentQualityAssessmentSerializer


class QualityFindingViewSet(ReadOnlyModelViewSet):
    queryset = QualityFinding.objects.select_related(
        "assessment", "document_version", "source_artifact"
    )
    serializer_class = QualityFindingSerializer


class KnowledgeSearchViewSet(GenericViewSet):
    def list(self, request):
        query_text = request.query_params.get("q", "").strip()
        mode = request.query_params.get("mode", "hybrid")
        if not query_text:
            return Response(
                {"detail": "The q query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if mode not in {"hybrid", "full_text", "vector"}:
            return Response(
                {"detail": "mode must be hybrid, full_text, or vector."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            limit = min(max(int(request.query_params.get("limit", "20")), 1), 50)
        except ValueError:
            return Response(
                {"detail": "limit must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        candidate_limit = max(limit * 4, 50)
        base = NormalizedSection.objects.filter(
            document_version__identity__superseded_by__isnull=True
        ).select_related(
            "document_version",
            "document_version__identity",
            "document_version__identity__collection",
            "document_version__identity__collection__authority",
            "source_artifact",
        )
        scores: dict[str, dict] = {}

        if mode in {"hybrid", "full_text"}:
            search_vector = SearchVector("heading", weight="A", config="simple") + SearchVector(
                "text", weight="B", config="simple"
            )
            search_query = SearchQuery(query_text, search_type="websearch", config="simple")
            text_results = (
                base.annotate(text_rank=SearchRank(search_vector, search_query))
                .filter(text_rank__gt=0)
                .order_by("-text_rank")[:candidate_limit]
            )
            for rank, section in enumerate(text_results, start=1):
                scores[str(section.id)] = {
                    "section": section,
                    "score": 1 / (60 + rank),
                    "text_rank": float(section.text_rank),
                    "vector_distance": None,
                }

        if mode in {"hybrid", "vector"}:
            provider, model, _ = embedding_configuration()
            query_vector = embed_text(query_text)
            if any(query_vector):
                vector_results = (
                    base.filter(embeddings__provider=provider, embeddings__model=model)
                    .annotate(vector_distance=CosineDistance("embeddings__embedding", query_vector))
                    .order_by("vector_distance")[:candidate_limit]
                )
                for rank, section in enumerate(vector_results, start=1):
                    entry = scores.setdefault(
                        str(section.id),
                        {
                            "section": section,
                            "score": 0.0,
                            "text_rank": None,
                            "vector_distance": None,
                        },
                    )
                    entry["score"] += 1 / (60 + rank)
                    entry["vector_distance"] = float(section.vector_distance)

        ranked = sorted(scores.values(), key=lambda item: item["score"], reverse=True)[:limit]
        results = []
        for entry in ranked:
            section = entry["section"]
            version = section.document_version
            identity = version.identity
            collection = identity.collection
            results.append(
                {
                    "section_id": str(section.id),
                    "heading": section.heading,
                    "text": section.text,
                    "page_number": section.page_number,
                    "source_locator": section.source_locator,
                    "score": entry["score"],
                    "text_rank": entry["text_rank"],
                    "vector_distance": entry["vector_distance"],
                    "document": {
                        "identity_id": str(identity.id),
                        "version_id": str(version.id),
                        "title": version.title or identity.canonical_title,
                        "canonical_url": version.canonical_url,
                        "content_sha256": version.normalized_content_sha256,
                    },
                    "authority": {
                        "id": str(collection.authority_id),
                        "name": collection.authority.name,
                        "trust_classification": collection.authority.trust_classification,
                    },
                    "collection": {"id": str(collection.id), "name": collection.name},
                    "artifact": {
                        "id": str(section.source_artifact_id),
                        "sha256": section.source_artifact.sha256,
                    },
                }
            )
        return Response({"query": query_text, "mode": mode, "results": results})
