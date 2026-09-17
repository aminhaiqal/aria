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
  applicability_taxonomy: {
    id: "66666666-6666-4666-8666-666666666666",
    name: "ARIA Malaysia business applicability",
    version: 1,
    disclaimer: "This vocabulary is not an official legal classification.",
    terms: [
      {
        id: "77777777-7777-4777-8777-777777777777",
        dimension: "regulated_role",
        code: "data-user",
        label: "Data user",
        description: "An organization acting as a data user.",
      },
    ],
  },
  business_profiles: [
    {
      id: "88888888-8888-4888-8888-888888888888",
      name: "Malaysia data team",
      notes: "",
      taxonomy_id: "66666666-6666-4666-8666-666666666666",
      is_active: true,
      term_ids: ["77777777-7777-4777-8777-777777777777"],
      updated_at: "2026-08-07T10:00:00Z",
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
  filters: {
    authority: "",
    collection: "",
    date_from: null,
    date_to: null,
    profile: "",
  },
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
    provider: "openrouter",
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
  selected_profile: {
    id: "88888888-8888-4888-8888-888888888888",
    name: "Malaysia data team",
    taxonomy_id: "66666666-6666-4666-8666-666666666666",
  },
  reviewed_impacts: [
    {
      id: "99999999-9999-4999-8999-999999999999",
      impact_type: "obligation",
      review_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
      review_decision: "approved",
      reviewed_title: "Technical safeguards may need review",
      reviewed_statement:
        "Data users may need to review their technical safeguards.",
      reviewed_effective_date_text: "",
      reviewed_at: "2026-08-07T10:00:00Z",
      comparison_item_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
      change_type: "modified",
      legal_effect_assessed: false,
      targets: [
        {
          id: "77777777-7777-4777-8777-777777777777",
          dimension: "regulated_role",
          code: "data-user",
          label: "Data user",
          disposition: "included",
          rationale: "The cited text names the role.",
        },
      ],
      evidence: [
        {
          side: "before",
          artifact_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          artifact_sha256: "b".repeat(64),
          anchor_text: "A data user shall use reasonable safeguards.",
          anchor_text_sha256: "c".repeat(64),
          section_text_sha256: "d".repeat(64),
          source_locator: { page: 2 },
        },
        {
          side: "after",
          artifact_id: "44444444-4444-4444-8444-444444444444",
          artifact_sha256: "a".repeat(64),
          anchor_text:
            "A data user shall use appropriate technical safeguards.",
          anchor_text_sha256: "e".repeat(64),
          section_text_sha256: "f".repeat(64),
          source_locator: { page: 2 },
        },
      ],
      relevance: {
        outcome: "matched",
        explanation:
          "The profile exactly matches every targeted applicability dimension.",
        matched_terms: [
          {
            id: "77777777-7777-4777-8777-777777777777",
            dimension: "regulated_role",
            code: "data-user",
            label: "Data user",
            description: "An organization acting as a data user.",
          },
        ],
        ruleset: "aria-exact-applicability-v1",
        evaluated_at: "2026-08-07T10:00:00Z",
      },
    },
  ],
}
