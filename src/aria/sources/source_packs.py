import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import soupsieve
from django.db import transaction
from django.utils import timezone

from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.models import DiscoveredCandidate, MonitoredResource
from aria.discovery.services import register_monitored_resource
from aria.events.services import record_audit_event, record_pipeline_event
from aria.fetching.client import hostname_is_allowed
from aria.sources.models import ConnectorConfiguration, SourceEndpoint, SourcePackSnapshot

PACK_ROOT = Path(__file__).with_name("packs")
PACK_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
HTML_ATTRIBUTE_PATTERN = re.compile(r"^[A-Za-z_:][A-Za-z0-9_.:-]*$")
QUERY_PARAMETER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
FORBIDDEN_KEY_PARTS = ("api_key", "credential", "password", "secret", "token")


class SourcePackError(ValueError):
    pass


@dataclass(frozen=True)
class SourcePack:
    slug: str
    schema_version: int
    version: int
    checksum: str
    definition: dict
    path: Path


@dataclass(frozen=True)
class SourcePackPlan:
    slug: str
    version: int
    checksum: str
    endpoint_id: str
    actions: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "slug": self.slug,
            "version": self.version,
            "checksum": self.checksum,
            "endpoint_id": self.endpoint_id,
            "action_count": len(self.actions),
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class SourcePackApplyResult:
    endpoint: SourceEndpoint
    snapshot: SourcePackSnapshot
    snapshot_created: bool
    resource_count: int
    backfilled_detail_count: int

    def as_dict(self) -> dict:
        return {
            "endpoint_id": str(self.endpoint.id),
            "endpoint_name": self.endpoint.name,
            "snapshot_id": str(self.snapshot.id),
            "snapshot_created": self.snapshot_created,
            "pack_slug": self.snapshot.pack_slug,
            "pack_version": self.snapshot.pack_version,
            "checksum": self.snapshot.checksum,
            "resource_count": self.resource_count,
            "backfilled_detail_count": self.backfilled_detail_count,
            "is_enabled": self.endpoint.is_enabled,
            "next_poll_at": (
                self.endpoint.next_poll_at.isoformat() if self.endpoint.next_poll_at else None
            ),
        }


def available_source_packs() -> tuple[str, ...]:
    return tuple(sorted(path.stem for path in PACK_ROOT.glob("*.json")))


def _canonical_bytes(definition: dict) -> bytes:
    return json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _require_keys(value: dict, keys: set[str], location: str) -> None:
    missing = sorted(keys - set(value))
    if missing:
        raise SourcePackError(f"{location} is missing: {', '.join(missing)}.")


def _reject_credentials(value, location: str = "source pack") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in FORBIDDEN_KEY_PARTS):
                raise SourcePackError(f"{location} contains forbidden credential key {key!r}.")
            _reject_credentials(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_credentials(item, f"{location}[{index}]")


def _validate_domain(domain: str, location: str) -> str:
    normalized = str(domain).rstrip(".").lower()
    if not normalized or "://" in normalized or "/" in normalized:
        raise SourcePackError(f"{location} must contain hostname-only values.")
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        raise SourcePackError(f"{location} cannot contain IP addresses.")
    if normalized == "localhost" or "." not in normalized:
        raise SourcePackError(f"{location} requires a public fully qualified hostname.")
    return normalized


def _validate_https_url(url: str, domains: list[str], location: str) -> None:
    parsed = urlsplit(str(url))
    if parsed.scheme != "https" or not parsed.hostname:
        raise SourcePackError(f"{location} must be an HTTPS URL.")
    if parsed.username or parsed.password or parsed.fragment:
        raise SourcePackError(f"{location} cannot contain credentials or a fragment.")
    if not hostname_is_allowed(parsed.hostname, domains):
        raise SourcePackError(f"{location} is outside the source allowlist.")


def _validate_paths(configuration: dict, key: str) -> None:
    for path in configuration.get(key, []):
        parsed = urlsplit(str(path))
        if (
            not parsed.path.startswith("/")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path.split("/")
        ):
            raise SourcePackError(f"connector configuration {key} requires exact safe paths.")


def _validate_detail_selectors(configuration: dict) -> None:
    selectors = configuration.get("detail_content_selectors")
    legacy = configuration.get("detail_content_selector")
    if selectors is not None and legacy is not None:
        raise SourcePackError("Declare either detail_content_selector or detail_content_selectors.")
    if selectors is None:
        selectors = [legacy] if legacy is not None else []
    if not isinstance(selectors, list) or len(selectors) > 5:
        raise SourcePackError("Detail selectors must be a list of at most five approved values.")
    for selector in selectors:
        if not isinstance(selector, str) or not selector.strip() or len(selector) > 200:
            raise SourcePackError("Each detail selector must be a non-empty bounded string.")
        try:
            soupsieve.compile(selector)
        except soupsieve.SelectorSyntaxError as error:
            raise SourcePackError(f"Invalid detail selector {selector!r}.") from error
    if configuration.get("follow_detail_pages") and not selectors:
        raise SourcePackError("Detail-page following requires an approved detail selector.")


def validate_source_pack(definition: dict) -> None:
    if not isinstance(definition, dict):
        raise SourcePackError("Source pack must be a JSON object.")
    _reject_credentials(definition)
    _require_keys(
        definition,
        {
            "schema_version",
            "slug",
            "version",
            "authority",
            "collection",
            "endpoint",
            "connector_configurations",
            "resources",
        },
        "source pack",
    )
    if definition["schema_version"] != 1:
        raise SourcePackError("Only source pack schema_version 1 is supported.")
    if not PACK_SLUG_PATTERN.fullmatch(str(definition["slug"])):
        raise SourcePackError("Source pack slug is invalid.")
    if not isinstance(definition["version"], int) or definition["version"] < 1:
        raise SourcePackError("Source pack version must be a positive integer.")

    authority = definition["authority"]
    collection = definition["collection"]
    endpoint = definition["endpoint"]
    for location, value in (
        ("authority", authority),
        ("collection", collection),
        ("endpoint", endpoint),
    ):
        if not isinstance(value, dict):
            raise SourcePackError(f"{location} must be an object.")
    _require_keys(
        authority,
        {
            "slug",
            "name",
            "jurisdiction",
            "country_code",
            "authority_type",
            "official_domains",
            "trust_classification",
        },
        "authority",
    )
    _require_keys(
        collection,
        {"slug", "name", "document_family", "priority"},
        "collection",
    )
    _require_keys(
        endpoint,
        {
            "name",
            "discovery_url",
            "connector_type",
            "allowed_domains",
            "polling_interval_minutes",
            "pagination_strategy",
            "requires_javascript",
            "connector_configuration_version",
            "default_enabled",
        },
        "endpoint",
    )
    if authority["authority_type"] not in Authority.AuthorityType.values:
        raise SourcePackError("authority.authority_type is unsupported.")
    if authority["trust_classification"] not in Authority.TrustClassification.values:
        raise SourcePackError("authority.trust_classification is unsupported.")
    if collection["document_family"] not in PublicationCollection.DocumentFamily.values:
        raise SourcePackError("collection.document_family is unsupported.")
    if collection["priority"] not in PublicationCollection.Priority.values:
        raise SourcePackError("collection.priority is unsupported.")
    if endpoint["connector_type"] not in SourceEndpoint.ConnectorType.values:
        raise SourcePackError("endpoint.connector_type is unsupported.")
    if endpoint["pagination_strategy"] not in SourceEndpoint.PaginationStrategy.values:
        raise SourcePackError("endpoint.pagination_strategy is unsupported.")
    if not isinstance(endpoint["polling_interval_minutes"], int) or not (
        5 <= endpoint["polling_interval_minutes"] <= 43200
    ):
        raise SourcePackError("endpoint polling interval must be between 5 and 43200 minutes.")
    if bool(endpoint["requires_javascript"]) != (
        endpoint["connector_type"] == SourceEndpoint.ConnectorType.JAVASCRIPT_LISTING
    ):
        raise SourcePackError("JavaScript requirement must match the connector type.")

    official_domains = [
        _validate_domain(value, "authority.official_domains")
        for value in authority["official_domains"]
    ]
    allowed_domains = [
        _validate_domain(value, "endpoint.allowed_domains") for value in endpoint["allowed_domains"]
    ]
    if not official_domains or not allowed_domains:
        raise SourcePackError("Official and allowed domains cannot be empty.")
    if any(not hostname_is_allowed(domain, official_domains) for domain in allowed_domains):
        raise SourcePackError("Every endpoint domain must belong to an official authority domain.")
    _validate_https_url(endpoint["discovery_url"], allowed_domains, "endpoint.discovery_url")

    configurations = definition["connector_configurations"]
    if not isinstance(configurations, list) or not configurations:
        raise SourcePackError("At least one connector configuration is required.")
    versions = [item.get("version") for item in configurations if isinstance(item, dict)]
    if len(versions) != len(configurations) or len(set(versions)) != len(versions):
        raise SourcePackError("Connector configuration versions must be unique objects.")
    if endpoint["connector_configuration_version"] not in versions:
        raise SourcePackError("Active endpoint configuration version is missing from the pack.")
    active_versions = [item["version"] for item in configurations if item.get("is_active")]
    if active_versions != [endpoint["connector_configuration_version"]]:
        raise SourcePackError("Exactly the endpoint configuration version must be active.")
    for item in configurations:
        _require_keys(item, {"version", "configuration", "is_active"}, "connector configuration")
        if not isinstance(item["version"], int) or item["version"] < 1:
            raise SourcePackError("Connector versions must be positive integers.")
        configuration = item["configuration"]
        if not isinstance(configuration, dict):
            raise SourcePackError("Connector configuration must be an object.")
        max_candidates = configuration.get("max_candidates")
        if not isinstance(max_candidates, int) or not 1 <= max_candidates <= 100:
            raise SourcePackError("Connector max_candidates must be between 1 and 100.")
        for key in (
            "include_path_prefixes",
            "upload_path_prefixes",
            "detail_resource_path_prefixes",
            "detail_final_path_prefixes",
            "browser_read_only_post_paths",
        ):
            _validate_paths(configuration, key)
        _validate_detail_selectors(configuration)
        link_attribute = configuration.get("link_attribute", "href")
        if not isinstance(link_attribute, str) or not HTML_ATTRIBUTE_PATTERN.fullmatch(
            link_attribute
        ):
            raise SourcePackError("Connector link_attribute must be a safe HTML attribute name.")
        prefix = configuration.get("link_value_prefix")
        suffix = configuration.get("link_value_suffix")
        if (prefix is None) != (suffix is None):
            raise SourcePackError(
                "Connector link value prefix and suffix must be declared together."
            )
        if prefix is not None and (
            not isinstance(prefix, str)
            or not isinstance(suffix, str)
            or not prefix
            or not suffix
            or len(prefix) > 128
            or len(suffix) > 128
        ):
            raise SourcePackError("Connector link value boundaries must be 1-128 characters.")
        wrapped_target = configuration.get("wrapped_link_target")
        if wrapped_target is not None:
            if not isinstance(wrapped_target, dict):
                raise SourcePackError("Connector wrapped_link_target must be an object.")
            expected_keys = {"path", "query_parameter", "encoding"}
            if set(wrapped_target) != expected_keys:
                raise SourcePackError(
                    "Connector wrapped_link_target requires only path, query_parameter, and "
                    "encoding."
                )
            _validate_paths({"paths": [wrapped_target["path"]]}, "paths")
            if not isinstance(
                wrapped_target["query_parameter"], str
            ) or not QUERY_PARAMETER_PATTERN.fullmatch(wrapped_target["query_parameter"]):
                raise SourcePackError(
                    "Connector wrapped link query_parameter must be a safe parameter name."
                )
            if wrapped_target["encoding"] != "base64_url_sha256_v1":
                raise SourcePackError("Connector wrapped link encoding is unsupported.")
        for domain in configuration.get("browser_dependency_domains", []):
            _validate_domain(domain, "browser_dependency_domains")
        host_aliases = configuration.get("canonical_host_aliases", {})
        if not isinstance(host_aliases, dict) or len(host_aliases) > 10:
            raise SourcePackError("Canonical host aliases must be an object of at most 10 values.")
        for source, target in host_aliases.items():
            normalized_source = _validate_domain(source, "canonical_host_aliases source")
            normalized_target = _validate_domain(target, "canonical_host_aliases target")
            if normalized_source == normalized_target:
                raise SourcePackError("Canonical host aliases must change the hostname.")
            if not hostname_is_allowed(
                normalized_source, allowed_domains
            ) or not hostname_is_allowed(normalized_target, allowed_domains):
                raise SourcePackError(
                    "Canonical host aliases must remain inside the endpoint allowlist."
                )
        if not isinstance(configuration.get("bootstrap_candidate_session", False), bool):
            raise SourcePackError("Connector session bootstrap must be a boolean.")

    if not isinstance(definition["resources"], list):
        raise SourcePackError("resources must be a list.")
    for resource in definition["resources"]:
        if not isinstance(resource, dict):
            raise SourcePackError("Each resource must be an object.")
        _require_keys(resource, {"resource_type", "url", "is_approved"}, "resource")
        if resource["resource_type"] not in MonitoredResource.ResourceType.values:
            raise SourcePackError("Resource type is unsupported.")
        _validate_https_url(resource["url"], allowed_domains, "resource.url")


def load_source_pack(slug: str) -> SourcePack:
    if not PACK_SLUG_PATTERN.fullmatch(str(slug)):
        raise SourcePackError("Source pack slug is invalid.")
    path = PACK_ROOT / f"{slug}.json"
    if not path.is_file():
        raise SourcePackError(f"Unknown source pack: {slug}.")
    try:
        definition = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourcePackError(f"Cannot read source pack {slug}: {error}.") from error
    validate_source_pack(definition)
    if definition["slug"] != slug:
        raise SourcePackError("Source pack filename and slug differ.")
    checksum = hashlib.sha256(_canonical_bytes(definition)).hexdigest()
    return SourcePack(
        slug=slug,
        schema_version=definition["schema_version"],
        version=definition["version"],
        checksum=checksum,
        definition=definition,
        path=path,
    )


def build_source_pack_plan(pack: SourcePack) -> SourcePackPlan:
    definition = pack.definition
    authority = Authority.objects.filter(slug=definition["authority"]["slug"]).first()
    collection = None
    endpoint = None
    if authority:
        collection = PublicationCollection.objects.filter(
            authority=authority,
            slug=definition["collection"]["slug"],
        ).first()
    if collection:
        endpoint = SourceEndpoint.objects.filter(
            collection=collection,
            name=definition["endpoint"]["name"],
        ).first()
    actions = []
    if not authority:
        actions.append("create_authority")
    if not collection:
        actions.append("create_collection")
    if not endpoint:
        actions.append(
            "create_disabled_endpoint"
            if not definition["endpoint"]["default_enabled"]
            else "create_enabled_endpoint"
        )
    else:
        actions.append("reconcile_endpoint_contract")
    for item in definition["connector_configurations"]:
        existing = (
            ConnectorConfiguration.objects.filter(
                endpoint=endpoint, version=item["version"]
            ).first()
            if endpoint
            else None
        )
        if existing and existing.configuration != item["configuration"]:
            raise SourcePackError(
                f"Connector version {item['version']} differs from immutable installed evidence; "
                "increment the connector and source-pack versions."
            )
        if not existing:
            actions.append(f"install_connector_v{item['version']}")
    if definition["resources"]:
        actions.append(f"reconcile_{len(definition['resources'])}_static_resources")
    if definition.get("detail_resource_backfill"):
        actions.append("reconcile_evidence_backfilled_details")
    snapshot = SourcePackSnapshot.objects.filter(checksum=pack.checksum).first()
    if not snapshot:
        conflicting_version = SourcePackSnapshot.objects.filter(
            pack_slug=pack.slug,
            pack_version=pack.version,
        ).first()
        if conflicting_version:
            raise SourcePackError(
                "Installed source-pack version has a different checksum; increment its version."
            )
        actions.append("record_immutable_pack_snapshot")
    return SourcePackPlan(
        slug=pack.slug,
        version=pack.version,
        checksum=pack.checksum,
        endpoint_id=str(endpoint.id) if endpoint else "",
        actions=tuple(actions),
    )


def _endpoint_defaults(definition: dict) -> dict:
    return {
        key: definition[key]
        for key in (
            "discovery_url",
            "connector_type",
            "allowed_domains",
            "polling_interval_minutes",
            "pagination_strategy",
            "expected_content_types",
            "requires_javascript",
            "connector_configuration_version",
        )
        if key in definition
    }


def _backfill_details(endpoint: SourceEndpoint, definition: dict) -> int:
    backfill = definition.get("detail_resource_backfill")
    if not backfill:
        return 0
    urls = {
        str(candidate.metadata_hints.get(backfill["metadata_url_key"])): str(
            candidate.metadata_hints.get(backfill.get("metadata_title_key", "title"), "")
        )
        for candidate in DiscoveredCandidate.objects.filter(endpoint=endpoint)
        if candidate.metadata_hints.get(backfill["metadata_url_key"])
    }
    for url, title in sorted(urls.items()):
        register_monitored_resource(
            endpoint,
            resource_type=backfill["resource_type"],
            url=url,
            title=title,
            is_approved=True,
            approval_basis=backfill["approval_basis"],
            polling_interval_minutes=backfill.get("polling_interval_minutes"),
            metadata={"source_listing": endpoint.discovery_url},
        )
    return len(urls)


@transaction.atomic
def apply_source_pack(
    pack: SourcePack,
    *,
    actor_identifier: str = "management_command",
) -> SourcePackApplyResult:
    build_source_pack_plan(pack)
    definition = pack.definition
    authority_values = dict(definition["authority"])
    authority_slug = authority_values.pop("slug")
    authority, _ = Authority.objects.update_or_create(
        slug=authority_slug,
        defaults=authority_values,
    )
    collection_values = dict(definition["collection"])
    collection_slug = collection_values.pop("slug")
    collection, _ = PublicationCollection.objects.update_or_create(
        authority=authority,
        slug=collection_slug,
        defaults=collection_values,
    )
    endpoint_definition = definition["endpoint"]
    endpoint_defaults = _endpoint_defaults(endpoint_definition)
    default_enabled = bool(endpoint_definition["default_enabled"])
    endpoint, endpoint_created = SourceEndpoint.objects.get_or_create(
        collection=collection,
        name=endpoint_definition["name"],
        defaults={
            **endpoint_defaults,
            "is_enabled": default_enabled,
            "health_state": (
                SourceEndpoint.HealthState.UNKNOWN
                if default_enabled
                else SourceEndpoint.HealthState.DISABLED
            ),
        },
    )
    if not endpoint_created:
        endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
        for key, value in endpoint_defaults.items():
            setattr(endpoint, key, value)
    if endpoint.is_enabled and endpoint.next_poll_at is None:
        endpoint.next_poll_at = timezone.now() + timedelta(
            minutes=endpoint.polling_interval_minutes
        )
    if not endpoint.is_enabled:
        endpoint.next_poll_at = None
        endpoint.health_state = SourceEndpoint.HealthState.DISABLED
    endpoint.full_clean()
    endpoint.save()

    configured_versions = set()
    for item in definition["connector_configurations"]:
        configured_versions.add(item["version"])
        configuration, created = ConnectorConfiguration.objects.get_or_create(
            endpoint=endpoint,
            version=item["version"],
            defaults={
                "configuration": item["configuration"],
                "notes": item.get("notes", ""),
                "is_active": item["is_active"],
            },
        )
        if not created:
            if configuration.configuration != item["configuration"]:
                raise SourcePackError(
                    f"Connector version {item['version']} changed without a version bump."
                )
            configuration.notes = item.get("notes", "")
            configuration.is_active = item["is_active"]
            configuration.save(update_fields=("notes", "is_active", "updated_at"))
    ConnectorConfiguration.objects.filter(endpoint=endpoint).exclude(
        version__in=configured_versions
    ).update(is_active=False)

    for resource in definition["resources"]:
        register_monitored_resource(
            endpoint,
            resource_type=resource["resource_type"],
            url=resource["url"],
            title=resource.get("title", ""),
            is_approved=resource["is_approved"],
            is_enabled=resource.get("is_enabled"),
            approval_basis=resource.get("approval_basis", ""),
            polling_interval_minutes=resource.get("polling_interval_minutes"),
            metadata=resource.get("metadata", {}),
        )
    backfilled = _backfill_details(endpoint, definition)
    snapshot, snapshot_created = SourcePackSnapshot.objects.get_or_create(
        checksum=pack.checksum,
        defaults={
            "endpoint": endpoint,
            "pack_slug": pack.slug,
            "schema_version": pack.schema_version,
            "pack_version": pack.version,
            "definition": definition,
        },
    )
    if snapshot.endpoint_id != endpoint.id:
        raise SourcePackError("Source pack checksum is already attached to another endpoint.")
    if snapshot_created:
        snapshot.full_clean()
        payload = {
            "snapshot_id": str(snapshot.id),
            "pack_slug": pack.slug,
            "pack_version": pack.version,
            "checksum": pack.checksum,
        }
        record_audit_event(
            action="source.pack.applied",
            target_type="source_endpoint",
            target_id=endpoint.id,
            actor_type="system",
            actor_identifier=actor_identifier,
            details=payload,
        )
        record_pipeline_event(
            event_type="source.pack.applied",
            aggregate_type="source_endpoint",
            aggregate_id=endpoint.id,
            payload=payload,
        )
    return SourcePackApplyResult(
        endpoint=endpoint,
        snapshot=snapshot,
        snapshot_created=snapshot_created,
        resource_count=endpoint.monitored_resources.count(),
        backfilled_detail_count=backfilled,
    )
