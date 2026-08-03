from rest_framework import serializers

from aria.artifacts.models import ArtifactObservation, RawArtifact
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.documents.models import DocumentIdentity, DocumentVersion, NormalizedSection
from aria.extraction.models import ExtractedDocument, ExtractionRun
from aria.fetching.models import FetchAttempt
from aria.knowledge.models import GraphEdge, GraphNode, SectionEmbedding
from aria.quality.models import (
    DocumentQualityAssessment,
    QualityAssessmentRun,
    QualityFinding,
)
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
            "last_successful_run_at",
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
            "retrieved_at",
            "connector_configuration_version",
            "etag",
            "last_modified",
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
