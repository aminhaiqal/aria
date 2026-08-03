from rest_framework.routers import DefaultRouter

from aria.api.views import (
    ArtifactObservationViewSet,
    AuthorityViewSet,
    DiscoveredCandidateViewSet,
    DocumentIdentityViewSet,
    DocumentVersionViewSet,
    ExtractedDocumentViewSet,
    ExtractionRunViewSet,
    FetchAttemptViewSet,
    GraphEdgeViewSet,
    GraphNodeViewSet,
    KnowledgeSearchViewSet,
    NormalizedSectionViewSet,
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
router.register("extraction-runs", ExtractionRunViewSet)
router.register("extracted-documents", ExtractedDocumentViewSet)
router.register("documents", DocumentIdentityViewSet)
router.register("document-versions", DocumentVersionViewSet)
router.register("sections", NormalizedSectionViewSet)
router.register("graph-nodes", GraphNodeViewSet)
router.register("graph-edges", GraphEdgeViewSet)
router.register("knowledge-search", KnowledgeSearchViewSet, basename="knowledge-search")

urlpatterns = router.urls
