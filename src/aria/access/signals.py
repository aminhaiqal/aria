from django.db.models.signals import post_migrate
from django.dispatch import receiver

from aria.access.roles import sync_operator_roles


@receiver(post_migrate, dispatch_uid="aria.access.sync_operator_roles")
def provision_operator_roles(sender, **kwargs) -> None:
    if sender.name == "aria.access":
        sync_operator_roles()
