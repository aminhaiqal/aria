import hashlib
import json
from dataclasses import asdict, dataclass

from django.db import transaction
from django.db.models import F

from aria.artifacts.models import ArtifactDerivative, ArtifactObservation, RawArtifact
from aria.discovery.models import SourceRun
from aria.documents.models import NormalizedSection, VersionEvidence
from aria.events.services import record_audit_event, record_pipeline_event
from aria.extraction.models import ExtractionRun
from aria.fetching.models import FetchAttempt
from aria.knowledge.embeddings import embedding_configuration
from aria.knowledge.models import GraphNode, SectionEmbedding
from aria.ocr.models import OCRRun
from aria.sources.models import SourceEndpoint


@dataclass(frozen=True)
class RepairAction:
    kind: str
    object_id: str
    source_run_id: str
    reason: str
    executable: bool = True


@dataclass(frozen=True)
class SourceRepairPlan:
    endpoint_id: str
    source_run_id: str
    actions: tuple[RepairAction, ...]

    @property
    def executable_count(self) -> int:
        return sum(action.executable for action in self.actions)

    def as_dict(self) -> dict:
        return {
            "endpoint_id": self.endpoint_id,
            "source_run_id": self.source_run_id,
            "action_count": len(self.actions),
            "executable_count": self.executable_count,
            "actions": [asdict(action) for action in self.actions],
        }

    @property
    def fingerprint(self) -> str:
        serialized = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode()).hexdigest()


def _latest_run(endpoint: SourceEndpoint):
    return (
        SourceRun.objects.filter(
            endpoint=endpoint,
            status=SourceRun.Status.COMPLETED,
            resource_run__isnull=True,
        )
        .order_by("-finished_at", "-created_at")
        .first()
    )


def build_source_repair_plan(endpoint: SourceEndpoint) -> SourceRepairPlan:
    source_run = _latest_run(endpoint)
    if source_run is None:
        return SourceRepairPlan(str(endpoint.id), "", ())
    candidates = list(
        source_run.candidate_observations.select_related("candidate").values_list(
            "candidate_id", flat=True
        )
    )
    actions: list[RepairAction] = []
    artifact_ids: set = set()
    for candidate_id in candidates:
        observations = list(
            ArtifactObservation.objects.filter(candidate_id=candidate_id)
            .order_by("-retrieved_at", "-id")
            .values_list("raw_artifact_id", flat=True)
        )
        if not observations:
            completed_fetch = FetchAttempt.objects.filter(
                candidate_id=candidate_id,
                source_run=source_run,
                status__in=(FetchAttempt.Status.SUCCEEDED, FetchAttempt.Status.NOT_MODIFIED),
            ).exists()
            actions.append(
                RepairAction(
                    kind="manual_review" if completed_fetch else "fetch_candidate",
                    object_id=str(candidate_id),
                    source_run_id=str(source_run.id),
                    reason=(
                        "Fetch is terminal but artifact evidence is missing."
                        if completed_fetch
                        else "Candidate has no archived artifact."
                    ),
                    executable=not completed_fetch,
                )
            )
            continue
        ocr_derivative_ids = set(
            ArtifactDerivative.objects.filter(
                source_artifact_id__in=observations,
                transformation_type=ArtifactDerivative.TransformationType.OCR_SEARCHABLE_PDF,
            ).values_list("derived_artifact_id", flat=True)
        )
        candidate_artifact_ids = set(observations) | ocr_derivative_ids
        artifact_ids.update(candidate_artifact_ids)
        successful = ExtractionRun.objects.filter(
            raw_artifact_id__in=candidate_artifact_ids,
            status=ExtractionRun.Status.SUCCEEDED,
        ).exists()
        active = ExtractionRun.objects.filter(
            raw_artifact_id__in=candidate_artifact_ids,
            status__in=(ExtractionRun.Status.PENDING, ExtractionRun.Status.RUNNING),
        ).exists()
        if not successful and not active:
            ocr_artifact_id = (
                ExtractionRun.objects.filter(
                    raw_artifact_id__in=observations,
                    status=ExtractionRun.Status.OCR_REQUIRED,
                )
                .order_by("-created_at", "-id")
                .values_list("raw_artifact_id", flat=True)
                .first()
            )
            if ocr_artifact_id:
                ocr_run = (
                    OCRRun.objects.filter(source_artifact_id=ocr_artifact_id)
                    .order_by("-created_at", "-id")
                    .first()
                )
                if ocr_run and ocr_run.status == OCRRun.Status.RUNNING:
                    continue
                if ocr_run and ocr_run.status == OCRRun.Status.PERMANENT_FAILURE:
                    actions.append(
                        RepairAction(
                            kind="manual_review",
                            object_id=str(ocr_artifact_id),
                            source_run_id=str(source_run.id),
                            reason="OCR reached a permanent failure and requires review.",
                            executable=False,
                        )
                    )
                elif ocr_run and ocr_run.status == OCRRun.Status.SUCCEEDED:
                    actions.append(
                        RepairAction(
                            kind="extract_artifact",
                            object_id=str(ocr_run.searchable_pdf_derivative.derived_artifact_id),
                            source_run_id=str(source_run.id),
                            reason=(
                                "Successful OCR derivative has no active or successful extraction."
                            ),
                        )
                    )
                else:
                    actions.append(
                        RepairAction(
                            kind="ocr_artifact",
                            object_id=str(ocr_artifact_id),
                            source_run_id=str(source_run.id),
                            reason="Archived artifact requires OCR before extraction.",
                        )
                    )
            else:
                actions.append(
                    RepairAction(
                        kind="extract_artifact",
                        object_id=str(observations[0]),
                        source_run_id=str(source_run.id),
                        reason="Archived artifact has no successful or active extraction.",
                    )
                )

    versions = list(
        VersionEvidence.objects.filter(raw_artifact_id__in=artifact_ids)
        .select_related("document_version")
        .values_list("document_version_id", flat=True)
        .distinct()
    )
    provider, model, _ = embedding_configuration()
    local_model = embedding_configuration("local_hash")[1]
    for version_id in versions:
        section_ids = list(
            NormalizedSection.objects.filter(document_version_id=version_id).values_list(
                "id", flat=True
            )
        )
        artifact_graph_ready = GraphNode.objects.filter(
            node_type=GraphNode.NodeType.VERSION,
            source_type="document_version",
            source_id=version_id,
        ).exists()
        local_count = (
            SectionEmbedding.objects.filter(
                normalized_section_id__in=section_ids,
                provider="local_hash",
                model=local_model,
                source_text_sha256=F("normalized_section__text_sha256"),
            )
            .values("normalized_section_id")
            .distinct()
            .count()
        )
        configured_count = (
            SectionEmbedding.objects.filter(
                normalized_section_id__in=section_ids,
                provider=provider,
                model=model,
                source_text_sha256=F("normalized_section__text_sha256"),
            )
            .values("normalized_section_id")
            .distinct()
            .count()
        )
        if (
            not artifact_graph_ready
            or not section_ids
            or local_count != len(section_ids)
            or configured_count != len(section_ids)
        ):
            actions.append(
                RepairAction(
                    kind="project_knowledge",
                    object_id=str(version_id),
                    source_run_id=str(source_run.id),
                    reason=(
                        "Document graph or current local/configured embedding projection "
                        "is incomplete."
                    ),
                )
            )
    unique = {(action.kind, action.object_id): action for action in actions}
    return SourceRepairPlan(
        endpoint_id=str(endpoint.id),
        source_run_id=str(source_run.id),
        actions=tuple(sorted(unique.values(), key=lambda item: (item.kind, item.object_id))),
    )


@transaction.atomic
def queue_source_repairs(
    endpoint: SourceEndpoint,
    plan: SourceRepairPlan,
    *,
    actor_identifier: str = "management_command",
) -> int:
    endpoint = SourceEndpoint.objects.select_for_update().get(pk=endpoint.pk)
    if str(endpoint.id) != plan.endpoint_id:
        raise ValueError("Repair plan does not belong to this endpoint.")
    current = build_source_repair_plan(endpoint)
    if current.fingerprint != plan.fingerprint:
        raise ValueError("Source repair plan changed; inspect a fresh dry run.")
    queued = 0
    for action in plan.actions:
        if not action.executable:
            continue
        if action.kind == "fetch_candidate":
            from aria.fetching.tasks import fetch_candidate

            transaction.on_commit(
                lambda action=action: fetch_candidate.delay(action.object_id, action.source_run_id)
            )
        elif action.kind == "extract_artifact":
            from aria.extraction.tasks import extract_raw_artifact

            transaction.on_commit(
                lambda action=action: extract_raw_artifact.delay(action.object_id)
            )
        elif action.kind == "project_knowledge":
            from aria.knowledge.tasks import project_document_version_knowledge

            transaction.on_commit(
                lambda action=action: project_document_version_knowledge.delay(action.object_id)
            )
        elif action.kind == "ocr_artifact":
            from aria.ocr.services import ensure_ocr_run
            from aria.ocr.tasks import process_ocr

            artifact = RawArtifact.objects.get(pk=action.object_id)
            ocr_run = ensure_ocr_run(artifact)
            transaction.on_commit(lambda ocr_run_id=str(ocr_run.id): process_ocr.delay(ocr_run_id))
        else:
            raise ValueError(f"Unsupported executable repair action: {action.kind}.")
        queued += 1
    payload = {**plan.as_dict(), "plan_fingerprint": plan.fingerprint, "queued": queued}
    record_audit_event(
        action="source.reliability.repair_queued",
        target_type="source_endpoint",
        target_id=endpoint.id,
        actor_type="system",
        actor_identifier=actor_identifier,
        details=payload,
    )
    record_pipeline_event(
        event_type="source.reliability.repair_queued",
        aggregate_type="source_endpoint",
        aggregate_id=endpoint.id,
        payload=payload,
    )
    return queued
