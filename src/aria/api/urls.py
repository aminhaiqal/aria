from rest_framework.routers import DefaultRouter

from aria.api.views import (
    ArtifactDerivativeViewSet,
    ArtifactObservationViewSet,
    AuthorityViewSet,
    DiscoveredCandidateViewSet,
    DocumentIdentityViewSet,
    DocumentQualityAssessmentViewSet,
    DocumentVersionViewSet,
    ExtractedDocumentViewSet,
    ExtractionRunViewSet,
    FetchAttemptViewSet,
    GraphEdgeViewSet,
    GraphNodeViewSet,
    KnowledgeSearchViewSet,
    NormalizedSectionViewSet,
    OCRRunViewSet,
    PublicationCollectionViewSet,
    QualityAssessmentRunViewSet,
    QualityFindingViewSet,
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
router.register("artifact-derivatives", ArtifactDerivativeViewSet)
router.register("extraction-runs", ExtractionRunViewSet)
router.register("extracted-documents", ExtractedDocumentViewSet)
router.register("ocr-runs", OCRRunViewSet)
router.register("documents", DocumentIdentityViewSet)
router.register("document-versions", DocumentVersionViewSet)
router.register("sections", NormalizedSectionViewSet)
router.register("graph-nodes", GraphNodeViewSet)
router.register("graph-edges", GraphEdgeViewSet)
router.register("knowledge-search", KnowledgeSearchViewSet, basename="knowledge-search")
router.register("quality-runs", QualityAssessmentRunViewSet)
router.register("quality-assessments", DocumentQualityAssessmentViewSet)
router.register("quality-findings", QualityFindingViewSet)

urlpatterns = router.urls
