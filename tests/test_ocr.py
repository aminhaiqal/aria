import hashlib
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from aria.artifacts.models import ArtifactDerivative, RawArtifact
from aria.artifacts.storage import ArtifactStorageError, FilesystemArtifactStore, build_artifact_key
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.connectors import CandidateData
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.discovery.services import create_source_run, observe_candidate
from aria.documents.models import DocumentIdentity, DocumentVersion, NormalizedSection
from aria.extraction.extractors import PDFExtractor
from aria.extraction.models import ExtractionRun
from aria.extraction.services import extract_artifact
from aria.fetching.client import FetchResponse
from aria.fetching.services import begin_fetch_attempt, complete_fetch
from aria.knowledge.models import SectionEmbedding
from aria.ocr.executor import OCRExecutionError, OCRResult
from aria.ocr.models import OCRRun
from aria.ocr.services import plan_collection_ocr, process_ocr_run
from aria.sources.models import SourceEndpoint


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream.set_data(f"BT /F1 11 Tf 72 720 Td ({escaped}) Tj ET".encode())
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


class SuccessfulFakeExecutor:
    def execute(self, content: bytes, configuration: dict) -> OCRResult:
        page_count = len(PdfReader(BytesIO(content)).pages)
        recognized = (
            "Akta Perlindungan Data Peribadi 2010 requires accountable personal data "
            "processing and security safeguards."
        )
        return OCRResult(
            searchable_pdf=text_pdf(recognized),
            text_sidecar=recognized.encode(),
            page_count=page_count,
            non_whitespace_characters=len(recognized.replace(" ", "")),
            toolchain={
                "ocrmypdf": "test-ocrmypdf",
                "tesseract": "test-tesseract",
                "languages": "eng+msa",
                "declared_toolchain": configuration["declared_toolchain"],
            },
        )


class FailingFakeExecutor:
    def execute(self, content: bytes, configuration: dict) -> OCRResult:
        raise OCRExecutionError("The scanned document could not be recognized.")


@override_settings(
    OBJECT_STORAGE_BACKEND="filesystem",
    EMBEDDING_PROVIDER="local_hash",
    LOCAL_EMBEDDING_MODEL="aria-token-hash-v1",
    EMBEDDING_DIMENSIONS=384,
    PDF_OCR_MIN_CHARACTERS_PER_PAGE=40,
    OCR_PROFILE_NAME="test-msa-eng",
    OCR_PROFILE_VERSION="1",
    OCR_DECLARED_TOOLCHAIN="test-toolchain-v1",
)
class OCRPipelineTestCase(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.storage_root = Path(self.temporary_directory.name)
        self.override = override_settings(OBJECT_STORAGE_ROOT=self.storage_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.authority = Authority.objects.create(
            name="Test OCR regulator",
            slug="test-ocr-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="OCR publications",
            slug="ocr-publications",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official OCR listing",
            discovery_url="https://example.com/notices/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["application/pdf"],
        )
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        document_url = "https://example.com/files/scanned-rule.pdf"
        self.identity_url = "https://example.com/notices/scanned-rule/"
        candidate, _ = observe_candidate(
            source_run,
            CandidateData(
                discovered_url=document_url,
                canonical_url=document_url,
                fingerprint="7" * 64,
                metadata_hints={"document_identity_url": self.identity_url},
            ),
        )
        source_content = blank_pdf()
        attempt = begin_fetch_attempt(candidate, source_run, request_headers={})
        observation = complete_fetch(
            attempt,
            FetchResponse(
                requested_url=document_url,
                final_url=document_url,
                status_code=200,
                headers={"content-type": "application/pdf"},
                redirect_chain=[],
                resolved_addresses=["93.184.216.34"],
                content=source_content,
            ),
            store=FilesystemArtifactStore(self.storage_root),
        )
        self.source_artifact = observation.raw_artifact
        self.source_content = source_content
        extraction_run = extract_artifact(self.source_artifact)
        self.assertEqual(extraction_run.status, ExtractionRun.Status.OCR_REQUIRED)

    def test_plan_selects_only_ocr_required_artifacts_idempotently(self) -> None:
        first = plan_collection_ocr(self.collection)
        replay = plan_collection_ocr(self.collection)

        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].source_artifact, self.source_artifact)
        self.assertEqual(first[0].ocr_run.id, replay[0].ocr_run.id)
        self.assertEqual(OCRRun.objects.count(), 1)
        self.assertEqual(first[0].ocr_run.configuration["languages"], "msa+eng")
        self.assertEqual(len(first[0].ocr_run.configuration_hash), 64)

        output = StringIO()
        call_command(
            "plan_ocr",
            authority=self.authority.slug,
            collection=self.collection.slug,
            stdout=output,
        )
        self.assertIn("OCR plan complete: count=1", output.getvalue())

    def test_successful_ocr_projects_immutable_source_and_derivative_lineage(self) -> None:
        run = plan_collection_ocr(self.collection)[0].ocr_run
        completed = process_ocr_run(
            run,
            executor=SuccessfulFakeExecutor(),
            dispatch_extraction=False,
        )
        extraction = extract_artifact(completed.searchable_pdf_derivative.derived_artifact)

        self.assertEqual(completed.status, OCRRun.Status.SUCCEEDED)
        self.assertEqual(extraction.status, ExtractionRun.Status.SUCCEEDED)
        self.assertEqual(RawArtifact.objects.count(), 3)
        self.assertEqual(ArtifactDerivative.objects.count(), 2)
        self.assertTrue(
            completed.searchable_pdf_derivative.derived_artifact.storage_key.startswith(
                "derived/ocr/sha256/"
            )
        )
        self.assertEqual(
            FilesystemArtifactStore(self.storage_root).read(self.source_artifact.storage_key),
            self.source_content,
        )
        self.assertEqual(
            hashlib.sha256(self.source_content).hexdigest(), self.source_artifact.sha256
        )

        identity = DocumentIdentity.objects.get(canonical_url=self.identity_url)
        version = DocumentVersion.objects.get(identity=identity)
        evidence = version.evidence_records.get()
        section = NormalizedSection.objects.get(document_version=version)
        self.assertEqual(evidence.raw_artifact, self.source_artifact)
        self.assertEqual(
            evidence.extraction_run.raw_artifact,
            completed.searchable_pdf_derivative.derived_artifact,
        )
        self.assertEqual(section.source_artifact, self.source_artifact)
        self.assertEqual(section.source_locator["artifact_sha256"], self.source_artifact.sha256)
        self.assertEqual(
            section.source_locator["derivative_artifact_sha256"],
            completed.searchable_pdf_derivative.derived_artifact.sha256,
        )
        self.assertEqual(section.source_locator["ocr_run_id"], str(completed.id))
        self.assertEqual(SectionEmbedding.objects.count(), 1)
        self.assertEqual(
            DiscoveredCandidate.objects.get().pipeline_state,
            DiscoveredCandidate.PipelineState.VERSIONED,
        )

        replay = process_ocr_run(
            completed,
            executor=SuccessfulFakeExecutor(),
            dispatch_extraction=False,
        )
        replay_extraction = extract_artifact(completed.searchable_pdf_derivative.derived_artifact)
        self.assertEqual(replay.id, completed.id)
        self.assertEqual(replay_extraction.id, extraction.id)
        self.assertEqual(OCRRun.objects.count(), 1)
        self.assertEqual(ArtifactDerivative.objects.count(), 2)
        self.assertEqual(DocumentVersion.objects.count(), 1)

    def test_queue_replays_missing_extraction_without_rerunning_ocr(self) -> None:
        run = plan_collection_ocr(self.collection)[0].ocr_run
        completed = process_ocr_run(
            run,
            executor=SuccessfulFakeExecutor(),
            dispatch_extraction=False,
        )
        derivative_artifact = completed.searchable_pdf_derivative.derived_artifact

        output = StringIO()
        with (
            patch("aria.ocr.management.commands.queue_ocr.process_ocr.delay") as queue_ocr,
            patch(
                "aria.ocr.management.commands.queue_ocr.extract_raw_artifact.delay"
            ) as queue_extraction,
        ):
            call_command(
                "queue_ocr",
                authority=self.authority.slug,
                collection=self.collection.slug,
                stdout=output,
            )

        queue_ocr.assert_not_called()
        queue_extraction.assert_called_once_with(str(derivative_artifact.id))
        self.assertIn("extraction_queued=1", output.getvalue())

        extract_artifact(derivative_artifact)
        with patch(
            "aria.ocr.management.commands.queue_ocr.extract_raw_artifact.delay"
        ) as queue_extraction:
            call_command(
                "queue_ocr",
                authority=self.authority.slug,
                collection=self.collection.slug,
            )
        queue_extraction.assert_not_called()

    def test_permanent_ocr_failure_is_recorded_without_derivatives(self) -> None:
        run = plan_collection_ocr(self.collection)[0].ocr_run
        failed = process_ocr_run(
            run,
            executor=FailingFakeExecutor(),
            dispatch_extraction=False,
        )

        self.assertEqual(failed.status, OCRRun.Status.PERMANENT_FAILURE)
        self.assertEqual(failed.error_code, "OCRExecutionError")
        self.assertIn("could not be recognized", failed.error_message)
        self.assertEqual(ArtifactDerivative.objects.count(), 0)
        self.assertEqual(RawArtifact.objects.count(), 1)

    def test_ocr_lineage_api_is_read_only(self) -> None:
        run = plan_collection_ocr(self.collection)[0].ocr_run
        run = process_ocr_run(
            run,
            executor=SuccessfulFakeExecutor(),
            dispatch_extraction=False,
        )
        user = get_user_model().objects.create_superuser(
            username="ocr-admin", password="test-password"
        )
        self.client.force_login(user)

        run_response = self.client.get(reverse("ocrrun-detail", args=[run.id]))
        derivative_response = self.client.get(reverse("artifactderivative-list"))

        self.assertEqual(run_response.status_code, 200)
        self.assertEqual(run_response.json()["source_sha256"], self.source_artifact.sha256)
        self.assertEqual(derivative_response.status_code, 200)
        self.assertEqual(derivative_response.json()["count"], 2)
        self.assertEqual(self.client.post(reverse("ocrrun-list"), {}).status_code, 405)

    def test_derived_storage_namespace_rejects_path_traversal(self) -> None:
        with self.assertRaisesMessage(ArtifactStorageError, "Artifact namespace is not safe"):
            build_artifact_key("a" * 64, namespace="../escape")

    def test_pdf_extractor_falls_back_when_ocr_text_has_no_layout_coordinates(self) -> None:
        recognized_text = (
            "Recognized OCR legal text with enough characters to pass deterministic review"
        )
        page = MagicMock()
        page.get.return_value = object()
        page.extract_text.side_effect = lambda **kwargs: (
            "" if kwargs.get("extraction_mode") == "layout" else recognized_text
        )
        reader = MagicMock()
        reader.is_encrypted = False
        reader.pages = [page]
        reader.metadata = {}

        with patch("aria.extraction.extractors.PdfReader", return_value=reader):
            extracted = PDFExtractor().extract(b"%PDF-1.7")

        self.assertFalse(extracted.requires_ocr)
        self.assertEqual(extracted.plain_text, recognized_text)
        self.assertEqual(extracted.metadata["plain_fallback_pages"], [1])
