from aria.access.roles import has_operator_permission


def operator_capabilities(request) -> dict:
    return {
        "operator_capabilities": {
            codename: has_operator_permission(request.user, codename)
            for codename in (
                "operate_sources",
                "operate_workflows",
                "review_changes",
                "review_impacts",
                "publish_changes",
                "publish_impacts",
                "view_audit_log",
            )
        }
    }
