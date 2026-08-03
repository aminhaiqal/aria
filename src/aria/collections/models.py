from django.db import models

from aria.authorities.models import Authority
from aria.common.models import TimeStampedModel


class PublicationCollection(TimeStampedModel):
    class DocumentFamily(models.TextChoices):
        ACTS_REGULATIONS = "acts_regulations", "Acts and regulations"
        POLICY = "policy", "Policy documents"
        CIRCULAR = "circular", "Circulars"
        GUIDELINE = "guideline", "Guidelines"
        CONSULTATION = "consultation", "Consultation papers"
        ENFORCEMENT = "enforcement", "Enforcement notices"
        PRESS_RELEASE = "press_release", "Press releases"
        FAQ = "faq", "FAQs"
        OTHER = "other", "Other"

    class Priority(models.IntegerChoices):
        CRITICAL = 10, "Critical"
        HIGH = 20, "High"
        NORMAL = 30, "Normal"
        LOW = 40, "Low"

    authority = models.ForeignKey(
        Authority,
        on_delete=models.PROTECT,
        related_name="publication_collections",
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    document_family = models.CharField(max_length=32, choices=DocumentFamily.choices)
    default_legal_status = models.CharField(max_length=128, blank=True)
    is_evidence_eligible = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(
        choices=Priority.choices,
        default=Priority.NORMAL,
    )
    expected_update_frequency = models.CharField(max_length=128, blank=True)
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("authority__name", "priority", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("authority", "slug"),
                name="unique_collection_slug_per_authority",
            )
        ]

    def __str__(self) -> str:
        return f"{self.authority}: {self.name}"
