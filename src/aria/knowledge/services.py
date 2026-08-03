import hashlib
import json

from django.db import transaction

from aria.documents.models import DocumentVersion, NormalizedSection
from aria.knowledge.embedding_services import project_section_embeddings
from aria.knowledge.embeddings import embedding_configuration
from aria.knowledge.models import GraphEdge, GraphNode


def _fingerprint(*parts: str) -> str:
    serialized = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _node(
    *,
    node_type: str,
    source_type: str,
    source_id,
    canonical_key: str,
    label: str,
    properties: dict | None = None,
) -> GraphNode:
    node, _ = GraphNode.objects.get_or_create(
        source_type=source_type,
        source_id=source_id,
        defaults={
            "node_type": node_type,
            "canonical_key": canonical_key,
            "label": label,
            "properties": properties or {},
        },
    )
    return node


def _edge(
    *,
    subject: GraphNode,
    predicate: str,
    object_node: GraphNode,
    source_type: str,
    source_id,
    evidence_version: DocumentVersion | None = None,
    evidence_section: NormalizedSection | None = None,
    evidence_artifact=None,
    properties: dict | None = None,
) -> GraphEdge:
    fingerprint = _fingerprint(
        str(subject.id),
        predicate,
        str(object_node.id),
        source_type,
        str(source_id),
    )
    edge, _ = GraphEdge.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={
            "subject": subject,
            "predicate": predicate,
            "object": object_node,
            "source_type": source_type,
            "source_id": source_id,
            "evidence_version": evidence_version,
            "evidence_section": evidence_section,
            "evidence_artifact": evidence_artifact,
            "properties": properties or {},
        },
    )
    return edge


@transaction.atomic
def project_document_version(version: DocumentVersion) -> None:
    identity = version.identity
    collection = identity.collection
    authority = collection.authority

    authority_node = _node(
        node_type=GraphNode.NodeType.AUTHORITY,
        source_type="authority",
        source_id=authority.id,
        canonical_key=f"authority:{authority.slug}",
        label=authority.name,
        properties={
            "country_code": authority.country_code,
            "trust_classification": authority.trust_classification,
        },
    )
    collection_node = _node(
        node_type=GraphNode.NodeType.COLLECTION,
        source_type="publication_collection",
        source_id=collection.id,
        canonical_key=f"collection:{authority.slug}:{collection.slug}",
        label=collection.name,
        properties={"document_family": collection.document_family},
    )
    identity_node = _node(
        node_type=GraphNode.NodeType.DOCUMENT,
        source_type="document_identity",
        source_id=identity.id,
        canonical_key=f"document:{identity.stable_key}",
        label=identity.canonical_title or identity.canonical_url,
        properties={"canonical_url": identity.canonical_url},
    )
    version_node = _node(
        node_type=GraphNode.NodeType.VERSION,
        source_type="document_version",
        source_id=version.id,
        canonical_key=(f"version:{identity.stable_key}:{version.normalized_content_sha256}"),
        label=version.title or identity.canonical_title or identity.canonical_url,
        properties={
            "canonical_url": version.canonical_url,
            "content_sha256": version.normalized_content_sha256,
            "language_hint": version.language_hint,
        },
    )
    _edge(
        subject=authority_node,
        predicate=GraphEdge.Predicate.HAS_COLLECTION,
        object_node=collection_node,
        source_type="publication_collection",
        source_id=collection.id,
    )
    _edge(
        subject=collection_node,
        predicate=GraphEdge.Predicate.HAS_DOCUMENT,
        object_node=identity_node,
        source_type="document_identity",
        source_id=identity.id,
    )
    _edge(
        subject=identity_node,
        predicate=GraphEdge.Predicate.HAS_VERSION,
        object_node=version_node,
        source_type="document_version",
        source_id=version.id,
        evidence_version=version,
    )

    for section in version.sections.select_related("source_artifact").all():
        section_node = _node(
            node_type=GraphNode.NodeType.SECTION,
            source_type="normalized_section",
            source_id=section.id,
            canonical_key=f"section:{section.id}",
            label=section.heading or section.text[:160],
            properties={
                "ordinal": section.ordinal,
                "page_number": section.page_number,
                "text_sha256": section.text_sha256,
            },
        )
        artifact = section.source_artifact
        artifact_node = _node(
            node_type=GraphNode.NodeType.ARTIFACT,
            source_type="raw_artifact",
            source_id=artifact.id,
            canonical_key=f"artifact:sha256:{artifact.sha256}",
            label=f"SHA-256 {artifact.sha256[:16]}…",
            properties={
                "sha256": artifact.sha256,
                "content_type": artifact.detected_content_type,
                "byte_size": artifact.byte_size,
            },
        )
        _edge(
            subject=version_node,
            predicate=GraphEdge.Predicate.HAS_SECTION,
            object_node=section_node,
            source_type="normalized_section",
            source_id=section.id,
            evidence_version=version,
            evidence_section=section,
            evidence_artifact=artifact,
        )
        _edge(
            subject=section_node,
            predicate=GraphEdge.Predicate.DERIVED_FROM,
            object_node=artifact_node,
            source_type="normalized_section",
            source_id=section.id,
            evidence_version=version,
            evidence_section=section,
            evidence_artifact=artifact,
        )
    for evidence in version.evidence_records.select_related("raw_artifact"):
        artifact = evidence.raw_artifact
        artifact_node = _node(
            node_type=GraphNode.NodeType.ARTIFACT,
            source_type="raw_artifact",
            source_id=artifact.id,
            canonical_key=f"artifact:sha256:{artifact.sha256}",
            label=f"SHA-256 {artifact.sha256[:16]}…",
            properties={
                "sha256": artifact.sha256,
                "content_type": artifact.detected_content_type,
                "byte_size": artifact.byte_size,
            },
        )
        _edge(
            subject=version_node,
            predicate=GraphEdge.Predicate.DERIVED_FROM,
            object_node=artifact_node,
            source_type="version_evidence",
            source_id=evidence.id,
            evidence_version=version,
            evidence_artifact=artifact,
            properties={"observed_url": evidence.observed_url},
        )

    provider, _, _ = embedding_configuration()
    sections = version.sections.order_by("id")
    project_section_embeddings(sections, provider_name="local_hash")
    if provider != "local_hash":
        from aria.knowledge.tasks import embed_document_version_sections

        transaction.on_commit(
            lambda: embed_document_version_sections.delay(str(version.id), provider)
        )
