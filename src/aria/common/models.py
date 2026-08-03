import uuid

from django.core.exceptions import ValidationError
from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(UUIDModel):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppendOnlyModel(UUIDModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs) -> None:
        if not self._state.adding:
            raise ValidationError(f"{type(self).__name__} records are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        raise ValidationError(f"{type(self).__name__} records are append-only.")
