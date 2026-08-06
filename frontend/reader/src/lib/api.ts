import type {
  DocumentPayload,
  ReaderBootstrap,
  ReaderOptions,
  SearchResponse,
} from "@/lib/types"

export class ReaderApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = "ReaderApiError"
    this.status = status
  }
}

export function readBootstrap(): ReaderBootstrap {
  const root = document.getElementById("aria-reader-root")
  const csrfToken = document
    .querySelector<HTMLMetaElement>('meta[name="csrf-token"]')
    ?.getAttribute("content")
  if (!root || !csrfToken) {
    throw new Error("ARIA reader bootstrap data is unavailable.")
  }
  const { dataset } = root
  const required = [
    "loginUrl",
    "logoutUrl",
    "optionsApi",
    "searchApi",
    "searchUrl",
    "userName",
  ] as const
  for (const key of required) {
    if (!dataset[key]) {
      throw new Error(`ARIA reader bootstrap field ${key} is unavailable.`)
    }
  }
  return {
    csrfToken,
    documentId: dataset.documentId ?? "",
    loginUrl: dataset.loginUrl!,
    logoutUrl: dataset.logoutUrl!,
    optionsApi: dataset.optionsApi!,
    searchApi: dataset.searchApi!,
    searchUrl: dataset.searchUrl!,
    userName: dataset.userName!,
    userStaff: dataset.userStaff === "true",
  }
}

async function requestJson<T>(
  url: string,
  loginUrl: string,
  signal?: AbortSignal
): Promise<T> {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  })
  if (response.status === 401 || response.status === 403) {
    const next = `${window.location.pathname}${window.location.search}`
    window.location.assign(`${loginUrl}?next=${encodeURIComponent(next)}`)
    throw new ReaderApiError(
      "Your reader session has expired.",
      response.status
    )
  }
  if (!response.ok) {
    let detail = `Reader request failed with status ${response.status}.`
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload.detail) detail = payload.detail
    } catch {
      // Preserve the bounded status message when the response is not JSON.
    }
    throw new ReaderApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export function fetchReaderOptions(
  bootstrap: ReaderBootstrap,
  signal?: AbortSignal
) {
  return requestJson<ReaderOptions>(
    bootstrap.optionsApi,
    bootstrap.loginUrl,
    signal
  )
}

export function searchReader(
  bootstrap: ReaderBootstrap,
  parameters: URLSearchParams,
  signal?: AbortSignal
) {
  return requestJson<SearchResponse>(
    `${bootstrap.searchApi}?${parameters.toString()}`,
    bootstrap.loginUrl,
    signal
  )
}

export function fetchDocument(
  bootstrap: ReaderBootstrap,
  documentId: string,
  signal?: AbortSignal
) {
  const base = bootstrap.searchApi.replace(/search\/$/, "documents/")
  return requestJson<DocumentPayload>(
    `${base}${encodeURIComponent(documentId)}/`,
    bootstrap.loginUrl,
    signal
  )
}
