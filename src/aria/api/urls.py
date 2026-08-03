from rest_framework.routers import DefaultRouter

from aria.api.views import (
    ArtifactObservationViewSet,
    AuthorityViewSet,
    DiscoveredCandidateViewSet,
    FetchAttemptViewSet,
    PublicationCollectionViewSet,
    RawArtifactViewSet,
    SourceEndpointViewSet,
    SourceRunViewSet,
)

router = DefaultRouter()
router.register("authorities", AuthorityViewSet)
router.register("collections", PublicationCollectionViewSet)
router.register("source-endpoints", SourceEndpointViewSet)
router.register("source-runs", SourceRunViewSet)
router.register("candidates", DiscoveredCandidateViewSet)
router.register("fetch-attempts", FetchAttemptViewSet)
router.register("artifacts", RawArtifactViewSet)
router.register("artifact-observations", ArtifactObservationViewSet)

urlpatterns = router.urls
