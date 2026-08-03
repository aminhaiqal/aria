import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from pypdf import PdfWriter

from aria.artifacts.storage import FilesystemArtifactStore
from aria.authorities.models import Authority
from aria.collections.models import PublicationCollection
from aria.discovery.connectors import CandidateData
from aria.discovery.models import DiscoveredCandidate, SourceRun
from aria.discovery.services import create_source_run, observe_candidate
from aria.documents.models import DocumentIdentity, DocumentVersion, NormalizedSection
from aria.extraction.extractors import HTMLExtractor, PDFExtractor
from aria.extraction.models import ExtractedDocument, ExtractionRun
from aria.extraction.services import extract_artifact
from aria.fetching.client import FetchResponse
from aria.fetching.services import begin_fetch_attempt, complete_fetch
from aria.knowledge.embeddings import embed_text
from aria.knowledge.models import GraphEdge, GraphNode, SectionEmbedding
from aria.sources.models import SourceEndpoint


class ExtractorTestCase(SimpleTestCase):
    def test_betterdocs_html_is_split_into_traceable_blocks(self) -> None:
        document = HTMLExtractor().extract(
            b"""
            <html lang="en"><head><title>Ignored site title</title></head><body>
              <nav>Navigation noise</nav>
              <div class="betterdocs-content-wrapper">
                <h1>Personal Data Protection Act</h1>
                <p>This official text explains the personal data protection obligations.</p>
                <h2>Application</h2>
                <p>The Act applies to specified processing activities in Malaysia.</p>
              </div>
            </body></html>
            """,
            source_url="https://example.com/act/",
        )

        self.assertEqual(document.title, "Personal Data Protection Act")
        self.assertEqual(document.metadata["content_selector"], ".betterdocs-content-wrapper")
        self.assertEqual(
            [block.block_type for block in document.blocks],
            [
                "heading",
                "paragraph",
                "heading",
                "paragraph",
            ],
        )
        self.assertTrue(document.blocks[-1].source_locator["html_path"])

    @override_settings(PDF_OCR_MIN_CHARACTERS_PER_PAGE=40)
    def test_textless_pdf_is_routed_to_ocr_review(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        content = BytesIO()
        writer.write(content)

        document = PDFExtractor().extract(content.getvalue())

        self.assertEqual(document.page_count, 1)
        self.assertTrue(document.requires_ocr)
        self.assertEqual(document.blocks, [])

    @override_settings(
        EMBEDDING_PROVIDER="local_hash",
        EMBEDDING_MODEL="aria-token-hash-v1",
        EMBEDDING_DIMENSIONS=384,
    )
    def test_local_embedding_is_deterministic_and_normalized(self) -> None:
        first = embed_text("personal data protection")
        second = embed_text("personal data protection")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 384)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0)


@override_settings(
    EMBEDDING_PROVIDER="local_hash",
    EMBEDDING_MODEL="aria-token-hash-v1",
    EMBEDDING_DIMENSIONS=384,
)
class ExtractionPipelineTestCase(TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.storage_root = Path(self.temporary_directory.name)
        self.authority = Authority.objects.create(
            name="Test official regulator",
            slug="test-official-regulator",
            jurisdiction="Malaysia",
            country_code="MY",
            authority_type=Authority.AuthorityType.REGULATOR,
            official_domains=["example.com"],
        )
        self.collection = PublicationCollection.objects.create(
            authority=self.authority,
            name="Official Acts",
            slug="official-acts",
            document_family=PublicationCollection.DocumentFamily.ACTS_REGULATIONS,
        )
        self.endpoint = SourceEndpoint.objects.create(
            collection=self.collection,
            name="Official Act listing",
            discovery_url="https://example.com/acts/",
            connector_type=SourceEndpoint.ConnectorType.HTML_LISTING,
            allowed_domains=["example.com"],
            expected_content_types=["text/html"],
        )
        source_run, _ = create_source_run(self.endpoint, trigger=SourceRun.Trigger.MANUAL)
        candidate, _ = observe_candidate(
            source_run,
            CandidateData(
                discovered_url="https://example.com/acts/act-709/",
                canonical_url="https://example.com/acts/act-709/",
                fingerprint="b" * 64,
                metadata_hints={"title": "Act 709"},
            ),
        )
        content = b"""
            <!doctype html><html lang="en"><body>
              <main><h1>Personal Data Protection Act 2010</h1>
              <p>The processing of personal data must comply with the Act and its principles.</p>
              </main>
            </body></html>
        """
        response = FetchResponse(
            requested_url=candidate.canonical_url,
            final_url=candidate.canonical_url,
            status_code=200,
            headers={"content-type": "text/html"},
            redirect_chain=[],
            resolved_addresses=["93.184.216.34"],
            content=content,
        )
        store = FilesystemArtifactStore(self.storage_root)
        attempt = begin_fetch_attempt(candidate, source_run, request_headers={})
        observation = complete_fetch(attempt, response, store=store)
        self.artifact = observation.raw_artifact

    def test_offline_extraction_versions_and_projects_the_evidence_graph(self) -> None:
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            run = extract_artifact(self.artifact)
            replay = extract_artifact(self.artifact)

        self.assertEqual(run.status, ExtractionRun.Status.SUCCEEDED)
        self.assertEqual(replay.id, run.id)
        self.assertEqual(ExtractionRun.objects.count(), 1)
        self.assertEqual(ExtractedDocument.objects.count(), 1)
        self.assertEqual(DocumentIdentity.objects.count(), 1)
        self.assertEqual(DocumentVersion.objects.count(), 1)
        self.assertEqual(NormalizedSection.objects.count(), 2)
        self.assertEqual(SectionEmbedding.objects.count(), 2)
        self.assertEqual(GraphNode.objects.count(), 7)
        self.assertEqual(GraphEdge.objects.count(), 8)
        self.assertEqual(
            set(GraphEdge.objects.values_list("predicate", flat=True)),
            {
                GraphEdge.Predicate.HAS_COLLECTION,
                GraphEdge.Predicate.HAS_DOCUMENT,
                GraphEdge.Predicate.HAS_VERSION,
                GraphEdge.Predicate.HAS_SECTION,
                GraphEdge.Predicate.DERIVED_FROM,
            },
        )
        self.assertEqual(
            self.artifact.sha256,
            hashlib.sha256(
                FilesystemArtifactStore(self.storage_root).read(self.artifact.storage_key)
            ).hexdigest(),
        )
        candidate = DiscoveredCandidate.objects.get()
        self.assertEqual(candidate.pipeline_state, DiscoveredCandidate.PipelineState.VERSIONED)

    def test_hybrid_search_returns_source_evidence(self) -> None:
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(self.artifact)
        user = get_user_model().objects.create_superuser(
            username="search-admin", password="test-password"
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("knowledge-search-list"),
            {"q": "personal data protection", "mode": "hybrid"},
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["artifact"]["sha256"], self.artifact.sha256)
        self.assertEqual(result["authority"]["name"], self.authority.name)
        self.assertEqual(result["document"]["canonical_url"], "https://example.com/acts/act-709/")

    def test_shared_artifact_projects_each_official_url_without_reextracting(self) -> None:
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            first_run = extract_artifact(self.artifact)
            source_run = self.artifact.observations.get().source_run
            second_candidate, _ = observe_candidate(
                source_run,
                CandidateData(
                    discovered_url="https://example.com/acts/separate-notice/",
                    canonical_url="https://example.com/acts/separate-notice/",
                    fingerprint="c" * 64,
                ),
            )
            content = FilesystemArtifactStore(self.storage_root).read(self.artifact.storage_key)
            second_attempt = begin_fetch_attempt(second_candidate, source_run, request_headers={})
            complete_fetch(
                second_attempt,
                FetchResponse(
                    requested_url=second_candidate.canonical_url,
                    final_url=second_candidate.canonical_url,
                    status_code=200,
                    headers={"content-type": "text/html"},
                    redirect_chain=[],
                    resolved_addresses=["93.184.216.34"],
                    content=content,
                ),
                store=FilesystemArtifactStore(self.storage_root),
            )
            replay = extract_artifact(self.artifact)

        self.assertEqual(first_run.id, replay.id)
        self.assertEqual(ExtractionRun.objects.count(), 1)
        self.assertEqual(ExtractedDocument.objects.count(), 1)
        self.assertEqual(DocumentIdentity.objects.count(), 2)
        self.assertEqual(DocumentVersion.objects.count(), 2)
        self.assertEqual(NormalizedSection.objects.count(), 4)
        self.assertEqual(
            set(DocumentIdentity.objects.values_list("canonical_url", flat=True)),
            {
                "https://example.com/acts/act-709/",
                "https://example.com/acts/separate-notice/",
            },
        )
