from rest_framework.viewsets import ReadOnlyModelViewSet

from aria.api.serializers import (
    AuthoritySerializer,
    DiscoveredCandidateSerializer,
    PublicationCollectionSerializer,
    SourceEndpointSerializer,
    SourceRunSerializer,
)
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import DiscoveredCandidate, SourceRun
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
