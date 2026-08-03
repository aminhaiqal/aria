from io import BytesIO

from django.http import FileResponse
from rest_framework.decorators import action
from rest_framework.viewsets import ReadOnlyModelViewSet

from aria.api.serializers import (
    ArtifactObservationSerializer,
    AuthoritySerializer,
    DiscoveredCandidateSerializer,
    FetchAttemptSerializer,
    PublicationCollectionSerializer,
    RawArtifactSerializer,
    SourceEndpointSerializer,
    SourceRunSerializer,
)
from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.artifacts.storage import get_artifact_store
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.fetching.models import FetchAttempt
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
