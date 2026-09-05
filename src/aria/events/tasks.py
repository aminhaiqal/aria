from celery import shared_task

from aria.events.delivery import deliver_pending_impact_webhooks


@shared_task
def deliver_reviewed_impact_webhooks() -> dict:
    return deliver_pending_impact_webhooks()
