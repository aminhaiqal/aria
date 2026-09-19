import type {
  DocumentPayload,
  ReaderBootstrap,
  ReaderOptions,
  SearchResponse,
  BusinessProfile,
  ChatThread,
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
    "chatApi",
    "loginUrl",
    "logoutUrl",
    "optionsApi",
    "profilesApi",
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
    chatApi: dataset.chatApi!,
    csrfToken,
    documentId: dataset.documentId ?? "",
    loginUrl: dataset.loginUrl!,
    logoutUrl: dataset.logoutUrl!,
    optionsApi: dataset.optionsApi!,
    profilesApi: dataset.profilesApi!,
    searchApi: dataset.searchApi!,
    searchUrl: dataset.searchUrl!,
    userName: dataset.userName!,
    userStaff: dataset.userStaff === "true",
  }
}

async function mutateJson<T>(
  url: string,
  bootstrap: ReaderBootstrap,
  method: "POST" | "PATCH",
  payload: Record<string, unknown>
): Promise<T> {
  const response = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRFToken": bootstrap.csrfToken,
    },
    body: JSON.stringify(payload),
  })
  if (response.status === 401 || response.status === 403) {
    const next = `${window.location.pathname}${window.location.search}`
    window.location.assign(`${bootstrap.loginUrl}?next=${encodeURIComponent(next)}`)
    throw new ReaderApiError("Your reader session has expired.", response.status)
  }
  if (!response.ok) {
    let detail = `Reader request failed with status ${response.status}.`
    try {
      const data = (await response.json()) as { detail?: string }
      if (data.detail) detail = data.detail
    } catch {
      // Keep the bounded status message for non-JSON errors.
    }
    throw new ReaderApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export function fetchChatThreads(
  bootstrap: ReaderBootstrap,
  signal?: AbortSignal
) {
  const parameters = new URLSearchParams()
  if (bootstrap.documentId) parameters.set("document", bootstrap.documentId)
  const suffix = parameters.size ? `?${parameters.toString()}` : ""
  return requestJson<{ results: ChatThread[] }>(
    `${bootstrap.chatApi}${suffix}`,
    bootstrap.loginUrl,
    signal
  )
}

export function fetchChatThread(
  bootstrap: ReaderBootstrap,
  threadId: string,
  signal?: AbortSignal
) {
  return requestJson<ChatThread>(
    `${bootstrap.chatApi}${encodeURIComponent(threadId)}/`,
    bootstrap.loginUrl,
    signal
  )
}

export function createChatThread(bootstrap: ReaderBootstrap) {
  return mutateJson<ChatThread>(bootstrap.chatApi, bootstrap, "POST", {
    document_id: bootstrap.documentId,
  })
}

export function sendChatMessage(
  bootstrap: ReaderBootstrap,
  threadId: string,
  question: string
) {
  return mutateJson<ChatThread>(
    `${bootstrap.chatApi}${encodeURIComponent(threadId)}/messages/`,
    bootstrap,
    "POST",
    { question }
  )
}

export function archiveChatThread(
  bootstrap: ReaderBootstrap,
  threadId: string
) {
  return mutateJson<ChatThread>(
    `${bootstrap.chatApi}${encodeURIComponent(threadId)}/`,
    bootstrap,
    "PATCH",
    { status: "archived" }
  )
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
  const profile = new URLSearchParams(window.location.search).get("profile")
  const parameters = profile ? `?profile=${encodeURIComponent(profile)}` : ""
  return requestJson<DocumentPayload>(
    `${base}${encodeURIComponent(documentId)}/${parameters}`,
    bootstrap.loginUrl,
    signal
  )
}

export async function createBusinessProfile(
  bootstrap: ReaderBootstrap,
  payload: { name: string; taxonomy_id: string; term_ids: string[] }
) {
  const response = await fetch(bootstrap.profilesApi, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRFToken": bootstrap.csrfToken,
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const data = (await response.json()) as { detail?: string }
    throw new ReaderApiError(
      data.detail || "The profile could not be saved.",
      response.status
    )
  }
  return (await response.json()) as {
    profile: BusinessProfile
    evaluation: Record<string, number | boolean>
  }
}
