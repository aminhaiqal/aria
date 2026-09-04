from rest_framework.routers import DefaultRouter

from aria.api.views import (
    ArtifactDerivativeViewSet,
    ArtifactObservationViewSet,
    AuthorityViewSet,
    BrowserCaptureViewSet,
    BrowserNetworkExchangeViewSet,
    ChangeOrchestrationViewSet,
    ComparisonItemViewSet,
    ComparisonReviewViewSet,
    ComparisonSummaryViewSet,
    DiscoveredCandidateViewSet,
    DocumentComparisonViewSet,
    DocumentIdentityViewSet,
    DocumentQualityAssessmentViewSet,
    DocumentVersionViewSet,
    EndpointObservationViewSet,
    ExtractedDocumentViewSet,
    ExtractionRunViewSet,
    FetchAttemptViewSet,
    GraphEdgeViewSet,
    GraphNodeViewSet,
    ImpactEvidenceViewSet,
    KnowledgeSearchViewSet,
    MonitoredResourceViewSet,
    NormalizedSectionViewSet,
    OCRRunViewSet,
    PublicationCollectionViewSet,
    QualityAssessmentRunViewSet,
    QualityFindingViewSet,
    RawArtifactViewSet,
    RegulatoryImpactViewSet,
    ResourceObservationViewSet,
    ResourceRunViewSet,
    ReviewedChangePublicationViewSet,
    SourceAdmissionAssessmentViewSet,
    SourceAdmissionPromotionViewSet,
    SourceEndpointViewSet,
    SourcePackSnapshotViewSet,
    SourceReliabilityAssessmentViewSet,
    SourceRunViewSet,
    StructuralAnchorViewSet,
    VersionLineageAssessmentViewSet,
)

router = DefaultRouter()
router.register("authorities", AuthorityViewSet)
router.register("collections", PublicationCollectionViewSet)
router.register("source-endpoints", SourceEndpointViewSet)
router.register("source-runs", SourceRunViewSet)
router.register("source-pack-snapshots", SourcePackSnapshotViewSet)
router.register("source-admissions", SourceAdmissionAssessmentViewSet)
router.register("source-promotions", SourceAdmissionPromotionViewSet)
router.register("source-reliability", SourceReliabilityAssessmentViewSet)
router.register("endpoint-observations", EndpointObservationViewSet)
router.register("candidates", DiscoveredCandidateViewSet)
router.register("monitored-resources", MonitoredResourceViewSet)
router.register("resource-runs", ResourceRunViewSet)
router.register("resource-observations", ResourceObservationViewSet)
router.register("fetch-attempts", FetchAttemptViewSet)
router.register("browser-captures", BrowserCaptureViewSet)
router.register("browser-network-exchanges", BrowserNetworkExchangeViewSet)
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
router.register("version-lineage", VersionLineageAssessmentViewSet)
router.register("structural-anchors", StructuralAnchorViewSet)
router.register("document-comparisons", DocumentComparisonViewSet)
router.register("comparison-items", ComparisonItemViewSet)
router.register("comparison-reviews", ComparisonReviewViewSet)
router.register("comparison-summaries", ComparisonSummaryViewSet)
router.register("reviewed-change-publications", ReviewedChangePublicationViewSet)
router.register("regulatory-impacts", RegulatoryImpactViewSet)
router.register("impact-evidence", ImpactEvidenceViewSet)
router.register("change-orchestrations", ChangeOrchestrationViewSet)

urlpatterns = router.urls
