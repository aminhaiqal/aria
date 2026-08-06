from rest_framework import serializers

from aria.artifacts.models import ArtifactDerivative, ArtifactObservation, RawArtifact
from aria.authorities.models import Authority
from aria.browser.models import BrowserCapture, BrowserNetworkExchange
from aria.collections.models import PublicationCollection
from aria.comparisons.models import (
    ComparisonItem,
    ComparisonReview,
    ComparisonSummary,
    DocumentComparison,
    ReviewedChangePublication,
    StructuralAnchor,
    VersionLineageAssessment,
)
from aria.discovery.models import (
    DiscoveredCandidate,
    EndpointObservation,
    MonitoredResource,
    ResourceLinkObservation,
    ResourceObservation,
    ResourceRun,
    SourceRun,
)
from aria.documents.models import DocumentIdentity, DocumentVersion, NormalizedSection
from aria.extraction.models import ExtractedDocument, ExtractionRun
from aria.fetching.models import FetchAttempt
from aria.knowledge.models import GraphEdge, GraphNode, SectionEmbedding
from aria.ocr.models import OCRRun
from aria.orchestration.models import ChangeOrchestration, OrchestrationStepAttempt
from aria.quality.models import (
    DocumentQualityAssessment,
    QualityAssessmentRun,
    QualityFinding,
)
from aria.reliability.models import SourceReliabilityAssessment
from aria.sources.models import ConnectorConfiguration, SourceEndpoint


class AuthoritySerializer(serializers.ModelSerializer):
    class Meta:
        model = Authority
        fields = (
            "id",
            "name",
            "slug",
            "aliases",
            "jurisdiction",
            "country_code",
            "authority_type",
            "regulatory_domains",
            "official_domains",
            "trust_classification",
            "is_enabled",
            "created_at",
            "updated_at",
        )


class PublicationCollectionSerializer(serializers.ModelSerializer):
    authority_name = serializers.CharField(source="authority.name", read_only=True)

    class Meta:
        model = PublicationCollection
        fields = (
            "id",
            "authority",
            "authority_name",
            "name",
            "slug",
            "document_family",
            "default_legal_status",
            "is_evidence_eligible",
            "priority",
            "expected_update_frequency",
            "is_enabled",
            "created_at",
            "updated_at",
        )


class ConnectorConfigurationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConnectorConfiguration
        fields = ("id", "version", "configuration", "notes", "is_active", "created_at")


class SourceEndpointSerializer(serializers.ModelSerializer):
    collection_name = serializers.CharField(source="collection.name", read_only=True)
    authority_name = serializers.CharField(source="collection.authority.name", read_only=True)
    connector_configurations = ConnectorConfigurationSerializer(many=True, read_only=True)

    class Meta:
        model = SourceEndpoint
        fields = (
            "id",
            "collection",
            "collection_name",
            "authority_name",
            "name",
            "discovery_url",
            "connector_type",
            "allowed_domains",
            "polling_interval_minutes",
            "next_poll_at",
            "pagination_strategy",
            "expected_content_types",
            "requires_javascript",
            "connector_configuration_version",
            "connector_configurations",
            "last_checked_at",
            "last_changed_at",
            "last_successful_run_at",
            "consecutive_failures",
            "health_state",
            "is_enabled",
            "created_at",
            "updated_at",
        )


class SourceRunSerializer(serializers.ModelSerializer):
    endpoint_name = serializers.CharField(source="endpoint.name", read_only=True)

    class Meta:
        model = SourceRun
        fields = (
            "id",
            "endpoint",
            "endpoint_name",
            "trigger",
            "status",
            "connector_configuration_version",
            "cursor_before",
            "cursor_after",
            "started_at",
            "finished_at",
            "discovered_candidate_count",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )


class SourceReliabilityAssessmentSerializer(serializers.ModelSerializer):
    endpoint_name = serializers.CharField(source="endpoint.name", read_only=True)

    class Meta:
        model = SourceReliabilityAssessment
        fields = (
            "id",
            "endpoint",
            "endpoint_name",
            "source_run",
            "previous_assessment",
            "status",
            "assessment_signature",
            "freshness_deadline",
            "candidate_set_sha256",
            "previous_candidate_set_sha256",
            "candidate_set_changed",
            "candidate_count",
            "artifact_ready_count",
            "extraction_ready_count",
            "graph_ready_count",
            "document_version_count",
            "section_count",
            "local_embedding_count",
            "configured_embedding_count",
            "configured_embedding_provider",
            "configured_embedding_model",
            "browser_capture_ready",
            "network_policy_ready",
            "findings",
            "assessed_at",
        )


class EndpointObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = EndpointObservation
        fields = (
            "id",
            "source_run",
            "endpoint",
            "previous_observation",
            "outcome",
            "requested_url",
            "final_url",
            "response_status",
            "request_headers",
            "response_headers",
            "redirect_chain",
            "resolved_addresses",
            "byte_size",
            "content_sha256",
            "etag",
            "last_modified",
            "connector_configuration_version",
            "checked_at",
        )


class DiscoveredCandidateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DiscoveredCandidate
        fields = (
            "id",
            "endpoint",
            "latest_source_run",
            "discovered_url",
            "canonical_url",
            "external_identifier",
            "fingerprint",
            "metadata_hints",
            "pipeline_state",
            "first_discovered_at",
            "last_discovered_at",
        )


class MonitoredResourceSerializer(serializers.ModelSerializer):
    endpoint_name = serializers.CharField(source="endpoint.name", read_only=True)

    class Meta:
        model = MonitoredResource
        fields = (
            "id",
            "endpoint",
            "endpoint_name",
            "parent",
            "resource_type",
            "url",
            "fingerprint",
            "title",
            "is_approved",
            "approval_basis",
            "is_enabled",
            "polling_interval_minutes",
            "next_poll_at",
            "last_checked_at",
            "last_changed_at",
            "last_successful_run_at",
            "consecutive_failures",
            "health_state",
            "metadata",
            "created_at",
            "updated_at",
        )


class ResourceRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceRun
        fields = (
            "id",
            "resource",
            "source_run",
            "status",
            "cursor_before",
            "cursor_after",
            "started_at",
            "finished_at",
            "observed_link_count",
            "accepted_candidate_count",
            "quarantined_link_count",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )


class ResourceLinkObservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceLinkObservation
        fields = (
            "id",
            "resource_observation",
            "target_url",
            "fingerprint",
            "title",
            "external_identifier",
            "relation",
            "state",
            "disposition",
            "quarantine_reason",
            "metadata",
            "created_at",
        )


class ResourceObservationSerializer(serializers.ModelSerializer):
    link_observations = ResourceLinkObservationSerializer(many=True, read_only=True)

    class Meta:
        model = ResourceObservation
        fields = (
            "id",
            "resource_run",
            "resource",
            "previous_observation",
            "outcome",
            "requested_url",
            "final_url",
            "response_status",
            "request_headers",
            "response_headers",
            "redirect_chain",
            "resolved_addresses",
            "byte_size",
            "content_sha256",
            "etag",
            "last_modified",
            "link_set_sha256",
            "link_count",
            "checked_at",
            "link_observations",
        )


class FetchAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = FetchAttempt
        fields = (
            "id",
            "candidate",
            "source_run",
            "attempt_number",
            "status",
            "requested_url",
            "final_url",
            "request_headers",
            "response_status",
            "response_headers",
            "redirect_chain",
            "resolved_addresses",
            "bytes_received",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
        )


class BrowserNetworkExchangeSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrowserNetworkExchange
        fields = (
            "id",
            "capture",
            "attempt_number",
            "sequence",
            "requested_url",
            "method",
            "resource_type",
            "disposition",
            "block_reason",
            "response_status",
            "content_type",
            "request_body_bytes",
            "request_body_sha256",
            "byte_size",
            "body_sha256",
            "body_artifact",
            "resolved_addresses",
            "occurred_at",
        )


class BrowserCaptureSerializer(serializers.ModelSerializer):
    network_exchanges = BrowserNetworkExchangeSerializer(many=True, read_only=True)

    class Meta:
        model = BrowserCapture
        fields = (
            "id",
            "source_run",
            "endpoint",
            "status",
            "profile",
            "configuration",
            "configuration_hash",
            "toolchain",
            "requested_url",
            "final_url",
            "response_status",
            "response_headers",
            "redirect_chain",
            "resolved_addresses",
            "original_artifact",
            "rendered_artifact",
            "rendered_derivative",
            "attempt_count",
            "request_count",
            "blocked_request_count",
            "response_bytes",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
            "network_exchanges",
            "created_at",
            "updated_at",
        )


class RawArtifactSerializer(serializers.ModelSerializer):
    observation_count = serializers.IntegerField(source="observations.count", read_only=True)

    class Meta:
        model = RawArtifact
        fields = (
            "id",
            "sha256",
            "byte_size",
            "detected_content_type",
            "storage_backend",
            "storage_key",
            "created_at",
            "observation_count",
        )


class ArtifactObservationSerializer(serializers.ModelSerializer):
    sha256 = serializers.CharField(source="raw_artifact.sha256", read_only=True)

    class Meta:
        model = ArtifactObservation
        fields = (
            "id",
            "raw_artifact",
            "sha256",
            "fetch_attempt",
            "candidate",
            "source_run",
            "requested_url",
            "final_url",
            "response_status",
            "response_headers",
            "redirect_chain",
            "content_changed",
            "retrieved_at",
            "connector_configuration_version",
            "etag",
            "last_modified",
        )


class ArtifactDerivativeSerializer(serializers.ModelSerializer):
    source_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)
    derived_sha256 = serializers.CharField(source="derived_artifact.sha256", read_only=True)

    class Meta:
        model = ArtifactDerivative
        fields = (
            "id",
            "source_artifact",
            "source_sha256",
            "derived_artifact",
            "derived_sha256",
            "transformation_type",
            "profile",
            "configuration_hash",
            "metadata",
            "created_at",
        )


class ExtractionRunSerializer(serializers.ModelSerializer):
    artifact_sha256 = serializers.CharField(source="raw_artifact.sha256", read_only=True)

    class Meta:
        model = ExtractionRun
        fields = (
            "id",
            "raw_artifact",
            "artifact_sha256",
            "extractor_name",
            "extractor_version",
            "configuration_hash",
            "status",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )


class ExtractedDocumentSerializer(serializers.ModelSerializer):
    block_count = serializers.IntegerField(source="blocks.count", read_only=True)
    artifact_sha256 = serializers.CharField(source="raw_artifact.sha256", read_only=True)

    class Meta:
        model = ExtractedDocument
        fields = (
            "id",
            "extraction_run",
            "raw_artifact",
            "artifact_sha256",
            "title",
            "language_hint",
            "plain_text_sha256",
            "metadata",
            "page_count",
            "requires_ocr",
            "block_count",
            "created_at",
        )


class OCRRunSerializer(serializers.ModelSerializer):
    source_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)

    class Meta:
        model = OCRRun
        fields = (
            "id",
            "source_artifact",
            "source_sha256",
            "profile_name",
            "profile_version",
            "configuration",
            "configuration_hash",
            "status",
            "searchable_pdf_derivative",
            "text_sidecar_derivative",
            "toolchain",
            "page_count",
            "non_whitespace_characters",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )


class DocumentIdentitySerializer(serializers.ModelSerializer):
    authority_name = serializers.CharField(source="collection.authority.name", read_only=True)
    collection_name = serializers.CharField(source="collection.name", read_only=True)
    version_count = serializers.IntegerField(source="versions.count", read_only=True)

    class Meta:
        model = DocumentIdentity
        fields = (
            "id",
            "collection",
            "authority_name",
            "collection_name",
            "stable_key",
            "canonical_title",
            "canonical_url",
            "identity_basis",
            "is_manual_override",
            "superseded_by",
            "supersession_basis",
            "version_count",
            "created_at",
            "updated_at",
        )


class DocumentVersionSerializer(serializers.ModelSerializer):
    identity_title = serializers.CharField(source="identity.canonical_title", read_only=True)
    section_count = serializers.IntegerField(source="sections.count", read_only=True)
    evidence_count = serializers.IntegerField(source="evidence_records.count", read_only=True)

    class Meta:
        model = DocumentVersion
        fields = (
            "id",
            "identity",
            "identity_title",
            "normalized_content_sha256",
            "title",
            "canonical_url",
            "language_hint",
            "normalized_metadata",
            "extractor_name",
            "extractor_version",
            "section_count",
            "evidence_count",
            "created_at",
        )


class NormalizedSectionSerializer(serializers.ModelSerializer):
    artifact_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)
    document_title = serializers.CharField(source="document_version.title", read_only=True)

    class Meta:
        model = NormalizedSection
        fields = (
            "id",
            "document_version",
            "document_title",
            "source_artifact",
            "artifact_sha256",
            "extraction_run",
            "ordinal",
            "section_type",
            "heading",
            "text",
            "text_sha256",
            "page_number",
            "char_start",
            "char_end",
            "source_locator",
            "created_at",
        )


class GraphNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = GraphNode
        fields = (
            "id",
            "node_type",
            "canonical_key",
            "label",
            "source_type",
            "source_id",
            "properties",
            "created_at",
            "updated_at",
        )


class GraphEdgeSerializer(serializers.ModelSerializer):
    subject_label = serializers.CharField(source="subject.label", read_only=True)
    object_label = serializers.CharField(source="object.label", read_only=True)

    class Meta:
        model = GraphEdge
        fields = (
            "id",
            "subject",
            "subject_label",
            "predicate",
            "object",
            "object_label",
            "source_type",
            "source_id",
            "evidence_version",
            "evidence_section",
            "evidence_artifact",
            "properties",
            "created_at",
        )


class SectionEmbeddingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SectionEmbedding
        fields = (
            "id",
            "normalized_section",
            "provider",
            "model",
            "dimensions",
            "source_text_sha256",
            "created_at",
        )


class QualityAssessmentRunSerializer(serializers.ModelSerializer):
    authority_name = serializers.CharField(source="collection.authority.name", read_only=True)
    collection_name = serializers.CharField(source="collection.name", read_only=True)

    class Meta:
        model = QualityAssessmentRun
        fields = (
            "id",
            "collection",
            "authority_name",
            "collection_name",
            "ruleset",
            "configuration",
            "configuration_hash",
            "corpus_fingerprint",
            "status",
            "started_at",
            "finished_at",
            "document_count",
            "passed_count",
            "warning_count",
            "review_required_count",
            "finding_count",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )


class DocumentQualityAssessmentSerializer(serializers.ModelSerializer):
    document_title = serializers.CharField(source="document_version.title", read_only=True)
    artifact_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)
    finding_count = serializers.IntegerField(source="findings.count", read_only=True)

    class Meta:
        model = DocumentQualityAssessment
        fields = (
            "id",
            "quality_run",
            "document_version",
            "document_title",
            "source_artifact",
            "artifact_sha256",
            "outcome",
            "score",
            "metrics",
            "finding_count",
            "created_at",
        )


class QualityFindingSerializer(serializers.ModelSerializer):
    document_title = serializers.CharField(source="document_version.title", read_only=True)
    artifact_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)

    class Meta:
        model = QualityFinding
        fields = (
            "id",
            "assessment",
            "document_version",
            "document_title",
            "source_artifact",
            "artifact_sha256",
            "code",
            "severity",
            "message",
            "evidence",
            "created_at",
        )


class VersionLineageAssessmentSerializer(serializers.ModelSerializer):
    source_artifact_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)

    class Meta:
        model = VersionLineageAssessment
        fields = (
            "id",
            "document_version",
            "representation_kind",
            "comparison_track_key",
            "provenance_status",
            "source_artifact",
            "source_artifact_sha256",
            "extraction_run",
            "ruleset",
            "configuration_hash",
            "basis",
            "created_at",
        )


class StructuralAnchorSerializer(serializers.ModelSerializer):
    source_artifact_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)

    class Meta:
        model = StructuralAnchor
        fields = (
            "id",
            "document_version",
            "normalized_section",
            "source_artifact",
            "source_artifact_sha256",
            "extraction_run",
            "ruleset",
            "configuration_hash",
            "anchor_type",
            "canonical_key",
            "ordinal",
            "label",
            "text",
            "text_sha256",
            "char_start",
            "char_end",
            "source_locator",
            "created_at",
        )


class ComparisonReviewSerializer(serializers.ModelSerializer):
    reviewer_username = serializers.CharField(source="reviewer.username", read_only=True)

    class Meta:
        model = ComparisonReview
        fields = (
            "id",
            "comparison_item",
            "decision",
            "rationale",
            "reviewer",
            "reviewer_username",
            "previous_review",
            "created_at",
        )


class ComparisonItemSerializer(serializers.ModelSerializer):
    current_review = serializers.SerializerMethodField()

    class Meta:
        model = ComparisonItem
        fields = (
            "id",
            "comparison",
            "before_anchor",
            "after_anchor",
            "change_type",
            "match_strategy",
            "similarity_score",
            "text_delta",
            "evidence",
            "fingerprint",
            "current_review",
            "created_at",
        )

    def get_current_review(self, obj):
        review = obj.reviews.order_by("-created_at", "-id").first()
        return ComparisonReviewSerializer(review).data if review else None


class DocumentComparisonSerializer(serializers.ModelSerializer):
    identity_title = serializers.CharField(source="identity.canonical_title", read_only=True)
    item_count = serializers.IntegerField(source="items.count", read_only=True)

    class Meta:
        model = DocumentComparison
        fields = (
            "id",
            "identity",
            "identity_title",
            "before_version",
            "after_version",
            "comparison_track_key",
            "ruleset",
            "configuration_hash",
            "input_fingerprint",
            "status",
            "unchanged_count",
            "added_count",
            "removed_count",
            "modified_count",
            "moved_count",
            "format_only_count",
            "ambiguous_count",
            "item_count",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )


class ComparisonSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ComparisonSummary
        fields = (
            "id",
            "comparison",
            "provider",
            "model",
            "prompt_version",
            "input_hash",
            "input_snapshot",
            "status",
            "output",
            "response_id",
            "input_tokens",
            "output_tokens",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
            "created_at",
            "updated_at",
        )


class ReviewedChangePublicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewedChangePublication
        fields = (
            "id",
            "comparison_item",
            "confirmation_review",
            "pipeline_event",
            "created_at",
        )


class OrchestrationStepAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrchestrationStepAttempt
        fields = (
            "id",
            "orchestration",
            "stage",
            "attempt_number",
            "outcome",
            "input_hash",
            "output",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
        )


class ChangeOrchestrationSerializer(serializers.ModelSerializer):
    artifact_sha256 = serializers.CharField(source="source_artifact.sha256", read_only=True)
    source_url = serializers.CharField(
        source="artifact_observation.final_url",
        read_only=True,
    )
    step_attempts = OrchestrationStepAttemptSerializer(many=True, read_only=True)

    class Meta:
        model = ChangeOrchestration
        fields = (
            "id",
            "artifact_observation",
            "source_artifact",
            "artifact_sha256",
            "source_url",
            "idempotency_key",
            "status",
            "current_stage",
            "extraction_run",
            "document_version",
            "quality_run",
            "lineage_assessment",
            "comparison",
            "summary",
            "started_at",
            "heartbeat_at",
            "finished_at",
            "retry_count",
            "error_code",
            "error_message",
            "step_attempts",
            "created_at",
            "updated_at",
        )
