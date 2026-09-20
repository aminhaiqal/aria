import {
  Archive,
  BookOpen,
  Bot,
  FileSearch,
  History,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Quote,
  Send,
  ShieldCheck,
  X,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  archiveChatThread,
  createChatThread,
  fetchChatThread,
  fetchChatThreads,
  sendChatMessage,
} from "@/lib/api"
import { shortHash } from "@/lib/format"
import type { ChatMessage, ChatThread, ReaderBootstrap } from "@/lib/types"

const documentPrompts = [
  "Summarise the main requirements in this document.",
  "What dates, deadlines, or transition periods are stated?",
  "Which passages describe who this document applies to?",
]

const corpusPrompts = [
  "Find current evidence about personal data protection.",
  "Which official documents discuss reporting obligations?",
  "What does the current evidence say about record keeping?",
]

function BodyPortal({ children }: { children: ReactNode }) {
  return createPortal(
    <div className="fixed inset-0 z-[70]" role="presentation">
      {children}
    </div>,
    document.body
  )
}

function MessageCard({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[86%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground">
          {message.content}
        </div>
      </div>
    )
  }

  return (
    <article className="grid gap-3" aria-label="ARIA answer">
      <div className="flex items-center gap-2 text-xs font-medium">
        <span className="grid size-6 place-items-center rounded-md bg-primary text-primary-foreground">
          <Bot className="size-3.5" />
        </span>
        ARIA
      </div>
      <div className="text-sm leading-6 whitespace-pre-wrap text-foreground/90">
        {message.content}
      </div>
      {message.citations.length > 0 ? (
        <div className="grid gap-2" aria-label="Evidence citations">
          {message.citations.map((citation) => (
            <a
              className="group rounded-xl border bg-muted/35 p-3 transition-colors hover:border-foreground/25 hover:bg-muted/65"
              href={citation.document.url}
              key={citation.id}
            >
              <div className="flex items-start gap-2">
                <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-background text-[0.65rem] font-semibold shadow-sm ring-1 ring-border">
                  {citation.number}
                </span>
                <div className="min-w-0">
                  <div className="truncate text-xs font-medium">
                    {citation.heading || `Section ${citation.section_ordinal}`}
                  </div>
                  <blockquote className="mt-1 line-clamp-3 text-xs leading-5 text-muted-foreground">
                    “{citation.excerpt}”
                  </blockquote>
                  <div className="mt-2 flex flex-wrap items-center gap-x-2 text-[0.65rem] text-muted-foreground">
                    <span className="truncate">{citation.document.title}</span>
                    {citation.page_number ? (
                      <span>Page {citation.page_number}</span>
                    ) : null}
                    <span className="font-mono">
                      {shortHash(citation.section_text_sha256, 10)}
                    </span>
                  </div>
                </div>
              </div>
            </a>
          ))}
        </div>
      ) : null}
    </article>
  )
}

export function ChatAssistant({ bootstrap }: { bootstrap: ReaderBootstrap }) {
  const [open, setOpen] = useState(false)
  const [threads, setThreads] = useState<ChatThread[]>([])
  const [activeThread, setActiveThread] = useState<ChatThread | null>(null)
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState("")
  const [question, setQuestion] = useState("")
  const [pendingQuestion, setPendingQuestion] = useState("")
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const transcriptEndRef = useRef<HTMLDivElement>(null)

  const replaceThreadSummary = useCallback((thread: ChatThread) => {
    setThreads((current) => [
      thread,
      ...current.filter((candidate) => candidate.id !== thread.id),
    ])
  }, [])

  const loadThread = useCallback(
    async (threadId: string, signal?: AbortSignal) => {
      const thread = await fetchChatThread(bootstrap, threadId, signal)
      setActiveThread(thread)
      replaceThreadSummary(thread)
      return thread
    },
    [bootstrap, replaceThreadSummary]
  )

  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    fetchChatThreads(bootstrap, controller.signal)
      .then(async ({ results }) => {
        setThreads(results)
        if (results[0]) {
          await loadThread(results[0].id, controller.signal)
        } else {
          setActiveThread(null)
        }
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Conversation history could not be loaded."
          )
        }
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [bootstrap, loadThread, open])

  useEffect(() => {
    if (!open) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false)
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = "hidden"
    window.addEventListener("keydown", handleKeyDown)
    const focusTimer = window.setTimeout(
      () => composerRef.current?.focus(),
      120
    )
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener("keydown", handleKeyDown)
      window.clearTimeout(focusTimer)
    }
  }, [open])

  useEffect(() => {
    const transcriptEnd = transcriptEndRef.current
    if (typeof transcriptEnd?.scrollIntoView === "function") {
      transcriptEnd.scrollIntoView({ block: "end" })
    }
  }, [activeThread?.messages, pendingQuestion, sending])

  async function selectThread(threadId: string) {
    setLoading(true)
    setError("")
    try {
      await loadThread(threadId)
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The conversation could not be loaded."
      )
    } finally {
      setLoading(false)
    }
  }

  async function archiveActiveThread() {
    if (!activeThread || sending) return
    setLoading(true)
    setError("")
    try {
      await archiveChatThread(bootstrap, activeThread.id)
      const remaining = threads.filter(
        (thread) => thread.id !== activeThread.id
      )
      setThreads(remaining)
      if (remaining[0]) await loadThread(remaining[0].id)
      else setActiveThread(null)
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The conversation could not be archived."
      )
    } finally {
      setLoading(false)
    }
  }

  async function submitQuestion(value?: string) {
    const submitted = (value ?? question).trim()
    if (!submitted || sending) return
    setSending(true)
    setError("")
    setQuestion("")
    setPendingQuestion(submitted)
    let thread = activeThread
    try {
      if (!thread) {
        thread = await createChatThread(bootstrap)
        setActiveThread(thread)
        replaceThreadSummary(thread)
      }
      const answered = await sendChatMessage(bootstrap, thread.id, submitted)
      setActiveThread(answered)
      replaceThreadSummary(answered)
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "ARIA could not answer this question."
      )
      if (thread) {
        try {
          await loadThread(thread.id)
        } catch {
          // Preserve the original generation error.
        }
      }
    } finally {
      setPendingQuestion("")
      setSending(false)
      window.setTimeout(() => composerRef.current?.focus(), 0)
    }
  }

  const prompts = bootstrap.documentId ? documentPrompts : corpusPrompts
  const messages = activeThread?.messages ?? []
  const contextTitle =
    activeThread?.document?.title ??
    (bootstrap.documentId ? "Current document" : "All current evidence")

  return (
    <>
      <Button
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => {
          setLoading(true)
          setError("")
          setOpen(true)
        }}
        variant="ghost"
      >
        <MessageSquareText data-icon="inline-start" />
        <span className="hidden sm:inline">Ask ARIA</span>
      </Button>

      {open ? (
        <BodyPortal>
          <button
            aria-label="Close Ask ARIA"
            className="absolute inset-0 bg-foreground/15 backdrop-blur-[1px]"
            onClick={() => setOpen(false)}
            type="button"
          />
          <section
            aria-labelledby="aria-chat-title"
            aria-modal="true"
            className="absolute inset-y-0 right-0 flex w-full flex-col border-l bg-background shadow-2xl sm:max-w-[31rem]"
            role="dialog"
          >
            <div className="border-b px-4 py-4 sm:px-5">
              <div className="flex items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-sm font-semibold text-primary-foreground">
                  A
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h2 className="font-semibold" id="aria-chat-title">
                      Ask ARIA
                    </h2>
                    <Badge variant="outline" className="text-[0.65rem]">
                      Evidence only
                    </Badge>
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    Answers remain linked to the official text.
                  </p>
                </div>
                <Button
                  aria-label="Close Ask ARIA"
                  onClick={() => setOpen(false)}
                  size="icon"
                  type="button"
                  variant="ghost"
                >
                  <X />
                </Button>
              </div>

              <div className="mt-4 flex items-center gap-2">
                <div className="relative min-w-0 flex-1">
                  <History className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                  <select
                    aria-label="Conversation"
                    className="h-9 w-full appearance-none rounded-md border bg-background pr-8 pl-9 text-sm outline-none focus:ring-2 focus:ring-ring/50"
                    disabled={threads.length === 0 || loading}
                    onChange={(event) => void selectThread(event.target.value)}
                    value={activeThread?.id ?? ""}
                  >
                    {!activeThread ? (
                      <option value="">New conversation</option>
                    ) : null}
                    {threads.map((thread) => (
                      <option key={thread.id} value={thread.id}>
                        {thread.title}
                      </option>
                    ))}
                  </select>
                </div>
                <Button
                  aria-label="Start a new conversation"
                  onClick={() => {
                    setActiveThread(null)
                    setError("")
                    window.setTimeout(() => composerRef.current?.focus(), 0)
                  }}
                  size="icon"
                  type="button"
                  variant="outline"
                >
                  <Plus />
                </Button>
                <Button
                  aria-label="Archive this conversation"
                  disabled={!activeThread || loading || sending}
                  onClick={() => void archiveActiveThread()}
                  size="icon"
                  type="button"
                  variant="outline"
                >
                  <Archive />
                </Button>
              </div>
            </div>

            <div className="border-b bg-muted/25 px-4 py-3 sm:px-5">
              <div className="flex items-start gap-2">
                {bootstrap.documentId ? (
                  <BookOpen className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                ) : (
                  <FileSearch className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                )}
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium">{contextTitle}</p>
                  <p className="mt-0.5 text-[0.68rem] text-muted-foreground">
                    {activeThread?.document
                      ? `Pinned to version ${shortHash(activeThread.document.version_sha256, 12)}`
                      : bootstrap.documentId
                        ? "A new thread will be pinned to this document version."
                        : "Searches current reader-eligible documents."}
                  </p>
                </div>
              </div>
            </div>

            <div
              aria-live="polite"
              className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-5"
            >
              {loading && messages.length === 0 ? (
                <div className="grid h-full place-items-center text-sm text-muted-foreground">
                  <span className="flex items-center gap-2">
                    <LoaderCircle className="size-4 animate-spin" /> Loading
                    conversation
                  </span>
                </div>
              ) : messages.length === 0 && !pendingQuestion ? (
                <div className="flex min-h-full flex-col justify-center py-8">
                  <div className="mx-auto grid size-12 place-items-center rounded-2xl border bg-muted/40">
                    <Quote className="size-5" />
                  </div>
                  <h3 className="mt-4 text-center text-lg font-semibold">
                    Start with the evidence
                  </h3>
                  <p className="mx-auto mt-2 max-w-sm text-center text-sm leading-6 text-muted-foreground">
                    ARIA retrieves relevant passages first, then answers with
                    links back to the exact source.
                  </p>
                  <div className="mt-6 grid gap-2">
                    {prompts.map((prompt) => (
                      <button
                        className="rounded-xl border bg-card px-4 py-3 text-left text-sm leading-5 transition-colors hover:bg-muted/60"
                        key={prompt}
                        onClick={() => void submitQuestion(prompt)}
                        type="button"
                      >
                        {prompt}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="grid gap-6">
                  {messages.map((message) => (
                    <MessageCard key={message.id} message={message} />
                  ))}
                  {pendingQuestion ? (
                    <div className="flex justify-end">
                      <div className="max-w-[86%] rounded-2xl rounded-br-md bg-primary px-4 py-3 text-sm leading-6 text-primary-foreground">
                        {pendingQuestion}
                      </div>
                    </div>
                  ) : null}
                  {sending ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <span className="grid size-6 place-items-center rounded-md bg-primary text-primary-foreground">
                        <Bot className="size-3.5" />
                      </span>
                      <span className="flex items-center gap-2">
                        <LoaderCircle className="size-3.5 animate-spin" />{" "}
                        Checking the evidence
                      </span>
                    </div>
                  ) : null}
                </div>
              )}
              <div ref={transcriptEndRef} />
            </div>

            <div className="border-t bg-background px-4 py-4 sm:px-5">
              {error ? (
                <Alert className="mb-3" variant="destructive">
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              ) : null}
              <form
                className="rounded-2xl border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/40"
                onSubmit={(event) => {
                  event.preventDefault()
                  void submitQuestion()
                }}
              >
                <textarea
                  aria-label="Ask about the evidence"
                  className="max-h-36 min-h-16 w-full resize-none bg-transparent px-2 py-1 text-sm leading-6 outline-none placeholder:text-muted-foreground"
                  disabled={sending}
                  maxLength={2000}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault()
                      void submitQuestion()
                    }
                  }}
                  placeholder={
                    bootstrap.documentId
                      ? "Ask about this document…"
                      : "Ask across current evidence…"
                  }
                  ref={composerRef}
                  value={question}
                />
                <div className="flex items-center justify-between gap-3 px-1 pb-1">
                  <span className="flex items-center gap-1.5 text-[0.65rem] text-muted-foreground">
                    <ShieldCheck className="size-3.5" /> Thread memory on
                  </span>
                  <Button
                    aria-label="Send question"
                    disabled={!question.trim() || sending}
                    size="icon"
                    type="submit"
                  >
                    {sending ? (
                      <LoaderCircle className="animate-spin" />
                    ) : (
                      <Send />
                    )}
                  </Button>
                </div>
              </form>
              <p className="mt-2 text-center text-[0.65rem] leading-4 text-muted-foreground">
                Questions and retrieved passages use the configured ZDR model
                route. Verify cited evidence; responses are not legal advice.
              </p>
            </div>
          </section>
        </BodyPortal>
      ) : null}
    </>
  )
}
