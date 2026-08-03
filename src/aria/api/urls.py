from rest_framework.routers import DefaultRouter

from aria.api.views import (
    AuthorityViewSet,
    DiscoveredCandidateViewSet,
    PublicationCollectionViewSet,
    SourceEndpointViewSet,
    SourceRunViewSet,
)

router = DefaultRouter()
router.register("authorities", AuthorityViewSet)
router.register("collections", PublicationCollectionViewSet)
router.register("source-endpoints", SourceEndpointViewSet)
router.register("source-runs", SourceRunViewSet)
router.register("candidates", DiscoveredCandidateViewSet)

urlpatterns = router.urls
