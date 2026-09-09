from django.db import models


class OperatorBoundary(models.Model):
    """Permission anchor; operator roles do not require a mutable domain table."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("access_console", "Can access the operator console"),
            ("operate_sources", "Can operate source admission and polling"),
            ("operate_workflows", "Can retry workflows and request summaries"),
            ("review_changes", "Can review textual changes"),
            ("review_impacts", "Can review regulatory impacts"),
            ("publish_changes", "Can publish reviewed textual changes"),
            ("publish_impacts", "Can publish reviewed regulatory impacts"),
            ("view_audit_log", "Can view the operator audit log"),
        )

    def __str__(self) -> str:
        return "ARIA operator permission boundary"
