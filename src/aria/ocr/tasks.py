from celery import shared_task

from aria.ocr.models import OCRRun
from aria.ocr.services import RetryableOCRError, process_ocr_run


@shared_task(
    bind=True,
    name="aria.ocr.tasks.process_ocr",
    autoretry_for=(RetryableOCRError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def process_ocr(self, ocr_run_id: str) -> str:
    run = OCRRun.objects.select_related("source_artifact").get(pk=ocr_run_id)
    return str(process_ocr_run(run).id)
