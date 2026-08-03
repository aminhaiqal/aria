from django.core.validators import MinLengthValidator, RegexValidator
from django.db import models

from aria.common.models import TimeStampedModel


class Authority(TimeStampedModel):
    class AuthorityType(models.TextChoices):
        REGULATOR = "regulator", "Regulator"
        MINISTRY = "ministry", "Ministry"
        LEGISLATURE = "legislature", "Legislature"
        GAZETTE = "gazette", "Gazette"
        COURT = "court", "Court"
        CENTRAL_BANK = "central_bank", "Central bank"
        OTHER = "other", "Other"

    class TrustClassification(models.TextChoices):
        AUTHORITATIVE = "authoritative", "Authoritative"
        OFFICIAL_SIGNAL = "official_signal", "Official signal"
        VERIFIED_SECONDARY = "verified_secondary", "Verified secondary"
        DISCOVERY_ONLY = "discovery_only", "Discovery only"
        UNTRUSTED = "untrusted", "Untrusted"

    name = models.CharField(max_length=255, unique=True)
    slug = models.SlugField(max_length=255, unique=True)
    aliases = models.JSONField(default=list, blank=True)
    jurisdiction = models.CharField(max_length=255)
    country_code = models.CharField(
        max_length=2,
        validators=[
            MinLengthValidator(2),
            RegexValidator(r"^[A-Z]{2}$", "Use an ISO 3166-1 alpha-2 uppercase code."),
        ],
    )
    authority_type = models.CharField(max_length=32, choices=AuthorityType.choices)
    regulatory_domains = models.JSONField(default=list, blank=True)
    official_domains = models.JSONField(default=list, blank=True)
    trust_classification = models.CharField(
        max_length=32,
        choices=TrustClassification.choices,
        default=TrustClassification.AUTHORITATIVE,
    )
    is_enabled = models.BooleanField(default=True)

    class Meta:
        ordering = ("country_code", "name")
        verbose_name_plural = "authorities"

    def __str__(self) -> str:
        return self.name
