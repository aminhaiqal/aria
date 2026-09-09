from collections.abc import Iterable
from functools import wraps
from hashlib import sha256
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.http import HttpResponseForbidden

from aria.events.services import record_audit_event

PERMISSION_PREFIX = "access."
OPERATOR_ROLES = {
    "ARIA Viewer": ("access_console",),
    "ARIA Source Operator": ("access_console", "operate_sources"),
    "ARIA Workflow Operator": ("access_console", "operate_workflows"),
    "ARIA Change Reviewer": ("access_console", "review_changes"),
    "ARIA Impact Reviewer": ("access_console", "review_impacts"),
    "ARIA Publisher": ("access_console", "publish_changes", "publish_impacts"),
    "ARIA Auditor": ("access_console", "view_audit_log"),
}


def sync_operator_roles() -> None:
    permissions = {
        permission.codename: permission
        for permission in Permission.objects.filter(
            content_type__app_label="access",
            codename__in={code for codes in OPERATOR_ROLES.values() for code in codes},
        )
    }
    expected = {code for codes in OPERATOR_ROLES.values() for code in codes}
    if permissions.keys() != expected:
        return
    for group_name, codes in OPERATOR_ROLES.items():
        group, _created = Group.objects.get_or_create(name=group_name)
        group.permissions.set(permissions[code] for code in codes)


def _base_operator_allowed(user) -> bool:
    return bool(user.is_authenticated and user.is_active and user.is_staff)


def has_operator_permission(user, codename: str) -> bool:
    if not _base_operator_allowed(user):
        return False
    if user.is_superuser or not settings.ENFORCE_OPERATOR_ROLES:
        return True
    return user.has_perm(f"{PERMISSION_PREFIX}{codename}")


def can_access_console(user) -> bool:
    return has_operator_permission(user, "access_console")


def _denial_target(request, route_arguments: Iterable[object]):
    for value in route_arguments:
        if getattr(value, "version", None) == 4:
            return value
    stable = sha256(request.path.encode("utf-8")).hexdigest()
    return uuid5(NAMESPACE_URL, f"aria-console-denial:{stable}")


def _audit_denial(request, codename: str, route_arguments: Iterable[object]) -> None:
    try:
        record_audit_event(
            action="console.permission_denied",
            target_type="operator_route",
            target_id=_denial_target(request, route_arguments),
            actor_type="user",
            actor_identifier=str(request.user.pk),
            details={
                "method": request.method,
                "path": request.path,
                "required_permission": f"{PERMISSION_PREFIX}{codename}",
            },
        )
    except Exception:
        # Authorization remains fail-closed if audit persistence is unavailable.
        pass


def operator_permission_required(codename: str):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if has_operator_permission(request.user, codename):
                return view(request, *args, **kwargs)
            _audit_denial(request, codename, (*args, *kwargs.values()))
            return HttpResponseForbidden("This operator role cannot perform that action.")

        return wrapped

    return decorator
