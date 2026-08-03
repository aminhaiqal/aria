from rest_framework import serializers

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import DiscoveredCandidate, SourceRun
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
