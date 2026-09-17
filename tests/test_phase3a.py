import hashlib
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

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
from aria.extraction.linked_documents import route_linked_publications
from aria.extraction.models import ExtractedDocument, ExtractionRun
from aria.extraction.services import extract_artifact
from aria.fetching.client import FetchResponse
from aria.fetching.services import begin_fetch_attempt, complete_fetch
from aria.knowledge.embedding_services import project_section_embeddings
from aria.knowledge.embeddings import EmbeddingBatch, embed_text
from aria.knowledge.evaluation import RetrievalCase, evaluate_embedding_provider
from aria.knowledge.models import GraphEdge, GraphNode, SectionEmbedding
from aria.knowledge.services import project_document_version
from aria.sources.models import SourceEndpoint


class FakeSemanticEmbeddingProvider:
    provider_name = "openrouter"
    model = "openai/text-embedding-3-small@aria-document-section-v2"
    dimensions = 384

    def __init__(self) -> None:
        self.calls = 0
        self.inputs: list[str] = []

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch:
        self.calls += 1
        self.inputs.extend(texts)
        vectors = []
        for index, _ in enumerate(texts):
            vector = [0.0] * self.dimensions
            vector[index % self.dimensions] = 1.0
            vectors.append(tuple(vector))
        return EmbeddingBatch(vectors=tuple(vectors), prompt_tokens=len(texts) * 3)


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

    def test_betterdocs_entry_content_excludes_sidebar_and_records_primary_document(self) -> None:
        document = HTMLExtractor().extract(
            b"""
            <html><head><title>Official Rule | JPDP</title></head><body>
              <div class="betterdocs-content-wrapper">
                <aside><h3>Navigation shell</h3><p>Unrelated category content</p></aside>
                <main><div class="betterdocs-entry-content">
                  <p>Official publication introduction and regulatory context.</p>
                  <div class="wp-block-file">
                    <a href="/uploads/official-rule.pdf">Official Rule PDF</a>
                    <a href="/uploads/official-rule.pdf">Download</a>
                  </div>
                </div></main>
              </div>
            </body></html>
            """,
            source_url="https://example.com/publications/official-rule/",
        )

        self.assertEqual(document.metadata["content_selector"], ".betterdocs-entry-content")
        self.assertNotIn("Navigation shell", document.plain_text)
        self.assertEqual(
            document.metadata["primary_document_links"],
            [
                {
                    "url": "https://example.com/uploads/official-rule.pdf",
                    "title": "Official Rule PDF",
                    "html_path": ("div:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)"),
                }
            ],
        )

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
        LOCAL_EMBEDDING_MODEL="aria-token-hash-v1",
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
    LOCAL_EMBEDDING_MODEL="aria-token-hash-v1",
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

    def test_openrouter_embeddings_are_parallel_idempotent_and_selectable(self) -> None:
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(self.artifact)
        sections = NormalizedSection.objects.order_by("id")
        provider = FakeSemanticEmbeddingProvider()

        first = project_section_embeddings(sections, provider=provider, batch_size=1)
        replay = project_section_embeddings(sections, provider=provider, batch_size=1)

        self.assertEqual(first.created_count, 2)
        self.assertEqual(first.prompt_tokens, 6)
        self.assertEqual(replay.created_count, 0)
        self.assertEqual(replay.skipped_count, 2)
        self.assertEqual(provider.calls, 2)
        self.assertTrue(
            all("Document title: Personal Data Protection Act" in text for text in provider.inputs)
        )
        self.assertEqual(SectionEmbedding.objects.filter(provider="openrouter").count(), 2)
        self.assertEqual(SectionEmbedding.objects.filter(provider="local_hash").count(), 2)

        user = get_user_model().objects.create_superuser(
            username="semantic-search-admin", password="test-password"
        )
        self.client.force_login(user)
        query_vector = [0.0] * 384
        query_vector[0] = 1.0
        with patch("aria.api.views.embed_text", return_value=query_vector):
            response = self.client.get(
                reverse("knowledge-search-list"),
                {
                    "q": "privacy obligations",
                    "mode": "vector",
                    "embedding_provider": "openrouter",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["embedding"]["provider"], "openrouter")
        self.assertEqual(response.json()["results"][0]["artifact"]["sha256"], self.artifact.sha256)

    @override_settings(
        EMBEDDING_PROVIDER="openrouter",
        OPENROUTER_EMBEDDING_MODEL="openai/text-embedding-3-small",
    )
    def test_openrouter_ingestion_keeps_local_fallback_and_queues_hosted_projection(self) -> None:
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(self.artifact)
        version = DocumentVersion.objects.get()
        template = version.sections.order_by("id").first()
        text = "A newly ingested official section keeps an offline fallback."
        section = NormalizedSection.objects.create(
            document_version=version,
            source_artifact=template.source_artifact,
            extraction_run=template.extraction_run,
            source_block=template.source_block,
            ordinal=version.sections.count() + 1,
            section_type="paragraph",
            heading="Fallback",
            text=text,
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            char_start=template.char_end,
            char_end=template.char_end + len(text),
            source_locator={"test": "fallback"},
        )

        with (
            patch("aria.knowledge.tasks.embed_document_version_sections.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            project_document_version(version)

        self.assertTrue(section.embeddings.filter(provider="local_hash").exists())
        delay.assert_called_once_with(str(version.id), "openrouter")

    def test_retrieval_evaluation_reports_ranked_evidence(self) -> None:
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(self.artifact)
        provider = FakeSemanticEmbeddingProvider()
        project_section_embeddings(
            NormalizedSection.objects.order_by("id"),
            provider=provider,
        )
        result = evaluate_embedding_provider(
            self.collection,
            provider_name="openrouter",
            provider=provider,
            cases=(
                RetrievalCase(
                    name="act",
                    query="Which law protects personal data?",
                    expected_url_suffix="acts/act-709/",
                ),
            ),
        )

        self.assertEqual(result["mean_reciprocal_rank"], 1.0)
        self.assertEqual(result["hit_at_1"], 1.0)
        self.assertEqual(result["cases"][0]["rank"], 1)

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

    def test_linked_artifact_uses_landing_page_identity_and_actual_observed_url(self) -> None:
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(self.artifact)
            source_run = self.artifact.observations.get().source_run
            identity_url = "https://example.com/acts/act-709/"
            document_url = "https://example.com/files/act-709-publication/"
            stale_identity = DocumentIdentity.objects.create(
                collection=self.collection,
                stable_key="9" * 64,
                canonical_title="Stale file identity",
                canonical_url=document_url,
                identity_basis={"strategy": "pre_link_routing"},
            )
            candidate, _ = observe_candidate(
                source_run,
                CandidateData(
                    discovered_url=document_url,
                    canonical_url=document_url,
                    fingerprint="d" * 64,
                    metadata_hints={"document_identity_url": identity_url},
                ),
            )
            content = b"""
                <html><body><main>
                  <h1>Personal Data Protection Act 2010</h1>
                  <p>This is a distinct archived representation of the official publication.</p>
                </main></body></html>
            """
            attempt = begin_fetch_attempt(candidate, source_run, request_headers={})
            observation = complete_fetch(
                attempt,
                FetchResponse(
                    requested_url=document_url,
                    final_url=document_url,
                    status_code=200,
                    headers={"content-type": "text/html"},
                    redirect_chain=[],
                    resolved_addresses=["93.184.216.34"],
                    content=content,
                ),
                store=FilesystemArtifactStore(self.storage_root),
            )
            extract_artifact(observation.raw_artifact)

        identity = DocumentIdentity.objects.get(canonical_url=identity_url)
        stale_identity.refresh_from_db()
        self.assertEqual(identity.canonical_url, identity_url)
        self.assertEqual(identity.versions.count(), 2)
        self.assertEqual(stale_identity.superseded_by, identity)
        self.assertEqual(
            stale_identity.supersession_basis["strategy"],
            "linked_document_identity_v1",
        )
        linked_evidence = identity.versions.get(
            normalized_content_sha256=hashlib.sha256(
                b"Personal Data Protection Act 2010\n\nThis is a distinct archived representation "
                b"of the official publication."
            ).hexdigest()
        ).evidence_records.get()
        self.assertEqual(linked_evidence.observed_url, document_url)

    def test_archived_primary_link_routing_is_idempotent(self) -> None:
        source_run = self.artifact.observations.get().source_run
        landing_url = "https://example.com/acts/linked-rule/"
        candidate, _ = observe_candidate(
            source_run,
            CandidateData(
                discovered_url=landing_url,
                canonical_url=landing_url,
                fingerprint="e" * 64,
            ),
        )
        content = b"""
            <html><body><div class="betterdocs-entry-content">
              <p>Official linked publication landing page.</p>
              <div class="wp-block-file">
                <a href="https://example.com/files/linked-rule.pdf">Linked Rule PDF</a>
                <a href="https://example.com/files/linked-rule.pdf">Download</a>
              </div>
            </div></body></html>
        """
        attempt = begin_fetch_attempt(candidate, source_run, request_headers={})
        observation = complete_fetch(
            attempt,
            FetchResponse(
                requested_url=landing_url,
                final_url=landing_url,
                status_code=200,
                headers={"content-type": "text/html"},
                redirect_chain=[],
                resolved_addresses=["93.184.216.34"],
                content=content,
            ),
            store=FilesystemArtifactStore(self.storage_root),
        )
        with override_settings(OBJECT_STORAGE_ROOT=self.storage_root):
            extract_artifact(observation.raw_artifact)

        first = route_linked_publications(self.collection)
        replay = route_linked_publications(self.collection)

        self.assertTrue(first.created)
        self.assertFalse(replay.created)
        self.assertEqual(first.source_run.id, replay.source_run.id)
        self.assertEqual(len(first.candidates), 1)
        routed = first.candidates[0]
        self.assertEqual(routed.canonical_url, "https://example.com/files/linked-rule.pdf")
        self.assertEqual(routed.metadata_hints["document_identity_url"], landing_url)
