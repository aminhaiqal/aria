import type {
  DocumentPayload,
  ReaderOptions,
  SearchResponse,
} from "@/lib/types"

export const readerOptions: ReaderOptions = {
  authorities: [
    {
      id: "authority-1",
      name: "Test Official Authority",
      slug: "test-official-authority",
      trust_classification: "authoritative",
    },
  ],
  collections: [
    {
      id: "collection-1",
      name: "Official Guidance",
      document_family: "guideline",
      authority_slug: "test-official-authority",
    },
  ],
}

export const searchResponse: SearchResponse = {
  query: "protect personal data",
  mode: "hybrid",
  embedding: {
    provider: "local_hash",
    model: "aria-token-hash-v1",
    dimensions: 384,
    query_sent_to_provider: false,
  },
  filters: { authority: "", collection: "", date_from: null, date_to: null },
  page: 1,
  page_size: 10,
  bounded_result_count: 1,
  has_previous: false,
  has_next: false,
  warnings: [],
  results: [
    {
      identity_id: "11111111-1111-4111-8111-111111111111",
      version_id: "22222222-2222-4222-8222-222222222222",
      title: "Official Data Processing Guidance",
      canonical_url: "https://example.test/guidance/data-processing/",
      normalized_content_sha256: "e".repeat(64),
      language_hint: "en",
      version_created_at: "2026-08-07T10:00:00Z",
      authority: {
        id: "authority-1",
        name: "Test Official Authority",
        slug: "test-official-authority",
        trust_classification: "authoritative",
      },
      collection: {
        id: "collection-1",
        name: "Official Guidance",
        document_family: "guideline",
      },
      score: 0.032,
      passages: [
        {
          section_id: "33333333-3333-4333-8333-333333333333",
          ordinal: 1,
          heading: "Protection principle",
          excerpt:
            "Organizations must protect personal data during processing.",
          excerpt_truncated: false,
          page_number: 2,
          source_locator: { page: 2 },
          score: 0.032,
          text_rank: 0.7,
          vector_distance: 0.1,
          artifact: {
            id: "44444444-4444-4444-8444-444444444444",
            sha256: "a".repeat(64),
            content_type: "application/pdf",
          },
        },
      ],
    },
  ],
}

export const documentResponse: DocumentPayload = {
  identity: {
    id: "11111111-1111-4111-8111-111111111111",
    title: "Official Data Processing Guidance",
    canonical_url: "https://example.test/guidance/data-processing/",
    identity_basis: { kind: "canonical_url" },
  },
  authority: {
    id: "authority-1",
    name: "Test Official Authority",
    slug: "test-official-authority",
    trust_classification: "authoritative",
  },
  collection: {
    id: "collection-1",
    name: "Official Guidance",
    document_family: "guideline",
    default_legal_status: "Official guidance",
  },
  version: {
    id: "22222222-2222-4222-8222-222222222222",
    normalized_content_sha256: "e".repeat(64),
    language_hint: "en",
    extractor_name: "test-pdf",
    extractor_version: "1",
    created_at: "2026-08-07T10:00:00Z",
  },
  versions: [
    {
      id: "22222222-2222-4222-8222-222222222222",
      normalized_content_sha256: "e".repeat(64),
      created_at: "2026-08-07T10:00:00Z",
      is_current: true,
    },
  ],
  section_count: 1,
  sections_truncated: false,
  sections: [
    {
      id: "33333333-3333-4333-8333-333333333333",
      ordinal: 1,
      section_type: "paragraph",
      heading: "Protection principle",
      text: "Organizations must protect personal data during processing.",
      text_sha256: "f".repeat(64),
      page_number: 2,
      source_locator: { page: 2 },
      artifact_id: "44444444-4444-4444-8444-444444444444",
    },
  ],
  evidence: [
    {
      artifact_id: "44444444-4444-4444-8444-444444444444",
      sha256: "a".repeat(64),
      byte_size: 4096,
      content_type: "application/pdf",
      stored_at: "2026-08-07T10:00:00Z",
      observed_url: "https://example.test/guidance/data-processing.pdf",
      retrieved_at: "2026-08-07T10:00:00Z",
      source_endpoint: "Official guidance listing",
      extraction: { name: "test-pdf", version: "1", status: "succeeded" },
    },
  ],
  quality: null,
  comparison: null,
  gpt_summary: {
    id: "55555555-5555-4555-8555-555555555555",
    provider: "openai",
    model: "gpt-test",
    prompt_version: "reader-test-v1",
    output: {
      title: "Reviewed textual change",
      overview: "One human-confirmed passage changed.",
      changes: [],
      caveats: ["Read the official evidence."],
      legal_effect_not_assessed: true,
    },
    finished_at: "2026-08-07T10:00:00Z",
  },
}
