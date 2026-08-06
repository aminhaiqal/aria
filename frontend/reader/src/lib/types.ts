export type TrustClassification = "authoritative" | "official" | string

export type ReaderBootstrap = {
  csrfToken: string
  documentId: string
  loginUrl: string
  logoutUrl: string
  optionsApi: string
  searchApi: string
  searchUrl: string
  userName: string
  userStaff: boolean
}

export type ReaderOptions = {
  authorities: Array<{
    id: string
    name: string
    slug: string
    trust_classification: TrustClassification
  }>
  collections: Array<{
    id: string
    name: string
    document_family: string
    authority_slug: string
  }>
}

export type SearchPassage = {
  section_id: string
  ordinal: number
  heading: string
  excerpt: string
  excerpt_truncated: boolean
  page_number: number | null
  source_locator: Record<string, unknown>
  score: number
  text_rank: number | null
  vector_distance: number | null
  artifact: {
    id: string
    sha256: string
    content_type: string
    official_url?: string
    retrieved_at?: string
    source_endpoint?: string
  }
}

export type SearchDocument = {
  identity_id: string
  version_id: string
  title: string
  canonical_url: string
  normalized_content_sha256: string
  language_hint: string
  version_created_at: string
  authority: {
    id: string
    name: string
    slug: string
    trust_classification: TrustClassification
  }
  collection: {
    id: string
    name: string
    document_family: string
  }
  score: number
  passages: SearchPassage[]
}

export type SearchResponse = {
  query: string
  mode: "hybrid" | "full_text" | "vector"
  embedding: {
    provider: string
    model: string
    dimensions: number
    query_sent_to_provider: boolean
  } | null
  filters: {
    authority: string
    collection: string
    date_from: string | null
    date_to: string | null
  }
  page: number
  page_size: number
  bounded_result_count: number
  has_previous: boolean
  has_next: boolean
  warnings: string[]
  results: SearchDocument[]
}

export type DocumentPayload = {
  identity: {
    id: string
    title: string
    canonical_url: string
    identity_basis: Record<string, unknown>
  }
  authority: {
    id: string
    name: string
    slug: string
    trust_classification: TrustClassification
  }
  collection: {
    id: string
    name: string
    document_family: string
    default_legal_status: string
  }
  version: {
    id: string
    normalized_content_sha256: string
    language_hint: string
    extractor_name: string
    extractor_version: string
    created_at: string
  }
  versions: Array<{
    id: string
    normalized_content_sha256: string
    created_at: string
    is_current: boolean
  }>
  section_count: number
  sections_truncated: boolean
  sections: Array<{
    id: string
    ordinal: number
    section_type: string
    heading: string
    text: string
    text_sha256: string
    page_number: number | null
    source_locator: Record<string, unknown>
    artifact_id: string
  }>
  evidence: Array<{
    artifact_id: string
    sha256: string
    byte_size: number
    content_type: string
    stored_at: string
    observed_url: string
    retrieved_at: string | null
    source_endpoint: string
    extraction: {
      name: string
      version: string
      status: string
    }
  }>
  quality: {
    outcome: string
    score: number
    metrics: Record<string, unknown>
    assessed_at: string
    findings: Array<{
      code: string
      severity: string
      message: string
    }>
  } | null
  comparison: {
    id: string
    before_version_id: string
    after_version_id: string
    finished_at: string
    counts: Record<string, number>
  } | null
  gpt_summary: {
    id: string
    provider: string
    model: string
    prompt_version: string
    output: {
      title?: string
      overview?: string
      changes?: Array<{
        change_type?: string
        explanation?: string
      }>
      caveats?: string[]
      legal_effect_not_assessed: true
    }
    finished_at: string
  } | null
}
