from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone
from pgvector.django import HnswIndex, VectorField

from aria.artifacts.models import RawArtifact
from aria.common.models import AppendOnlyModel, TimeStampedModel
from aria.documents.models import DocumentVersion, NormalizedSection


class GraphNode(TimeStampedModel):
    class NodeType(models.TextChoices):
        AUTHORITY = "authority", "Authority"
        COLLECTION = "collection", "Collection"
        DOCUMENT = "document", "Document identity"
        VERSION = "version", "Document version"
        SECTION = "section", "Normalized section"
        ARTIFACT = "artifact", "Raw artifact"

    node_type = models.CharField(max_length=32, choices=NodeType.choices, db_index=True)
    canonical_key = models.CharField(max_length=255, unique=True)
    label = models.TextField(blank=True)
    source_type = models.CharField(max_length=64)
    source_id = models.UUIDField()
    properties = models.JSONField(default=dict)

    class Meta:
        ordering = ("node_type", "label", "canonical_key")
        constraints = [
            models.UniqueConstraint(
                fields=("source_type", "source_id"),
                name="unique_graph_node_source",
            )
        ]
        indexes = [models.Index(fields=("node_type", "source_id"))]

    def __str__(self) -> str:
        return self.label or self.canonical_key


class GraphEdge(AppendOnlyModel):
    class Predicate(models.TextChoices):
        HAS_COLLECTION = "has_collection", "Has collection"
        HAS_DOCUMENT = "has_document", "Has document"
        HAS_VERSION = "has_version", "Has version"
        HAS_SECTION = "has_section", "Has section"
        DERIVED_FROM = "derived_from", "Derived from"

    subject = models.ForeignKey(
        GraphNode,
        on_delete=models.PROTECT,
        related_name="outgoing_edges",
    )
    predicate = models.CharField(max_length=64, choices=Predicate.choices, db_index=True)
    object = models.ForeignKey(
        GraphNode,
        on_delete=models.PROTECT,
        related_name="incoming_edges",
    )
    fingerprint = models.CharField(
        max_length=64,
        unique=True,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    source_type = models.CharField(max_length=64)
    source_id = models.UUIDField()
    evidence_version = models.ForeignKey(
        DocumentVersion,
        on_delete=models.PROTECT,
        related_name="graph_edges",
        null=True,
        blank=True,
    )
    evidence_section = models.ForeignKey(
        NormalizedSection,
        on_delete=models.PROTECT,
        related_name="graph_edges",
        null=True,
        blank=True,
    )
    evidence_artifact = models.ForeignKey(
        RawArtifact,
        on_delete=models.PROTECT,
        related_name="graph_edges",
        null=True,
        blank=True,
    )
    properties = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("subject", "predicate", "object")
        indexes = [
            models.Index(fields=("subject", "predicate")),
            models.Index(fields=("object", "predicate")),
            models.Index(fields=("source_type", "source_id")),
        ]

    def __str__(self) -> str:
        return f"{self.subject_id} -[{self.predicate}]-> {self.object_id}"


class SectionEmbedding(AppendOnlyModel):
    normalized_section = models.ForeignKey(
        NormalizedSection,
        on_delete=models.PROTECT,
        related_name="embeddings",
    )
    provider = models.CharField(max_length=64)
    model = models.CharField(max_length=128)
    dimensions = models.PositiveIntegerField()
    source_text_sha256 = models.CharField(
        max_length=64,
        validators=[RegexValidator(r"^[0-9a-f]{64}$")],
    )
    embedding = VectorField(dimensions=384)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "normalized_section",
                    "provider",
                    "model",
                    "source_text_sha256",
                ),
                name="unique_section_embedding_projection",
            )
        ]
        indexes = [
            HnswIndex(
                name="section_embedding_cosine_hnsw",
                fields=("embedding",),
                m=16,
                ef_construction=64,
                opclasses=("vector_cosine_ops",),
            ),
            models.Index(fields=("provider", "model")),
        ]

    def __str__(self) -> str:
        return f"{self.normalized_section_id} {self.model}"
