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
  return createPortal(children, document.body)
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

  function startNewConversation() {
    setActiveThread(null)
    setError("")
    window.setTimeout(() => composerRef.current?.focus(), 0)
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
          <section
            aria-labelledby="aria-chat-title"
            aria-modal="true"
            className="fixed inset-0 z-[70] flex overflow-hidden bg-background"
            role="dialog"
          >
            <div className="hidden w-72 shrink-0 flex-col border-r bg-muted/30 md:flex">
              <div className="flex h-16 items-center gap-3 px-4">
                <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-xs font-semibold text-primary-foreground">
                  A
                </span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold">ARIA</p>
                  <p className="text-xs text-muted-foreground">
                    Official evidence chat
                  </p>
                </div>
              </div>

              <div className="px-3 pb-3">
                <Button
                  className="h-10 w-full justify-start gap-2 border bg-background px-3 shadow-xs"
                  onClick={startNewConversation}
                  type="button"
                  variant="outline"
                >
                  <Plus className="size-4" /> New conversation
                </Button>
              </div>

              <nav
                aria-label="Conversations"
                className="min-h-0 flex-1 overflow-y-auto px-2 pb-4"
              >
                <div className="flex items-center gap-2 px-2 py-3 text-xs font-medium text-muted-foreground">
                  <History className="size-3.5" /> Conversations
                </div>
                {loading && threads.length === 0 ? (
                  <div className="flex items-center gap-2 px-3 py-2 text-xs text-muted-foreground">
                    <LoaderCircle className="size-3.5 animate-spin" /> Loading
                  </div>
                ) : threads.length === 0 ? (
                  <p className="px-3 py-2 text-xs leading-5 text-muted-foreground">
                    Your evidence conversations will appear here.
                  </p>
                ) : (
                  <div className="grid gap-1">
                    {threads.map((thread) => {
                      const isActive = activeThread?.id === thread.id
                      return (
                        <div className="group relative" key={thread.id}>
                          <button
                            aria-current={isActive ? "page" : undefined}
                            className={`flex h-10 w-full items-center gap-2 rounded-lg px-3 pr-10 text-left text-sm transition-colors ${
                              isActive
                                ? "bg-background font-medium shadow-xs"
                                : "text-foreground/80 hover:bg-background/65"
                            }`}
                            onClick={() => void selectThread(thread.id)}
                            type="button"
                          >
                            <MessageSquareText className="size-3.5 shrink-0 text-muted-foreground" />
                            <span className="truncate">{thread.title}</span>
                          </button>
                          {isActive ? (
                            <Button
                              aria-label="Archive this conversation"
                              className="absolute top-1.5 right-1.5"
                              disabled={loading || sending}
                              onClick={() => void archiveActiveThread()}
                              size="icon-sm"
                              type="button"
                              variant="ghost"
                            >
                              <Archive />
                            </Button>
                          ) : null}
                        </div>
                      )
                    })}
                  </div>
                )}
              </nav>

              <div className="border-t p-3">
                <div className="rounded-xl border bg-background/75 p-3">
                  <div className="flex items-start gap-2.5">
                    {bootstrap.documentId ? (
                      <BookOpen className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    ) : (
                      <FileSearch className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    )}
                    <div className="min-w-0">
                      <p className="truncate text-xs font-medium">
                        {contextTitle}
                      </p>
                      <p className="mt-1 text-[0.68rem] leading-4 text-muted-foreground">
                        {activeThread?.document
                          ? `Pinned to version ${shortHash(activeThread.document.version_sha256, 12)}`
                          : bootstrap.documentId
                            ? "New chats use this document version."
                            : "Searches current reader-eligible documents."}
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="flex min-w-0 flex-1 flex-col bg-background">
              <div className="flex h-16 shrink-0 items-center gap-3 border-b px-4 sm:px-6">
                <div className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-xs font-semibold text-primary-foreground md:hidden">
                  A
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h2 className="font-semibold" id="aria-chat-title">
                      Ask ARIA
                    </h2>
                    <Badge className="text-[0.65rem]" variant="outline">
                      Evidence only
                    </Badge>
                  </div>
                  <p className="truncate text-xs text-muted-foreground">
                    {contextTitle}
                  </p>
                </div>
                <Button
                  aria-label="Close Ask ARIA"
                  onClick={() => setOpen(false)}
                  size="icon-lg"
                  type="button"
                  variant="ghost"
                >
                  <X />
                </Button>
              </div>

              <div className="flex items-center gap-2 border-b px-3 py-2 md:hidden">
                <div className="relative min-w-0 flex-1">
                  <History className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
                  <select
                    aria-label="Conversation"
                    className="h-9 w-full appearance-none rounded-lg border bg-background pr-8 pl-9 text-sm outline-none focus:ring-2 focus:ring-ring/50"
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
                  onClick={startNewConversation}
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

              <div
                aria-live="polite"
                className="min-h-0 flex-1 overflow-y-auto"
              >
                <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col px-4 py-8 sm:px-6 sm:py-10">
                  {loading && messages.length === 0 ? (
                    <div className="grid flex-1 place-items-center text-sm text-muted-foreground">
                      <span className="flex items-center gap-2">
                        <LoaderCircle className="size-4 animate-spin" /> Loading
                        conversation
                      </span>
                    </div>
                  ) : messages.length === 0 && !pendingQuestion ? (
                    <div className="flex flex-1 flex-col justify-center py-8">
                      <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-primary text-primary-foreground shadow-sm">
                        <Quote className="size-5" />
                      </div>
                      <h3 className="mt-5 text-center text-2xl font-semibold tracking-tight">
                        Start with the evidence
                      </h3>
                      <p className="mx-auto mt-2 max-w-md text-center text-sm leading-6 text-muted-foreground">
                        Ask a direct question. ARIA will retrieve the relevant
                        passages and link its answer to the official text.
                      </p>
                      <div className="mx-auto mt-7 grid w-full max-w-2xl gap-2 sm:grid-cols-2">
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
                    <div className="grid gap-8 py-2">
                      {messages.map((message) => (
                        <MessageCard key={message.id} message={message} />
                      ))}
                      {pendingQuestion ? (
                        <div className="flex justify-end">
                          <div className="max-w-[86%] rounded-3xl rounded-br-lg bg-muted px-4 py-3 text-sm leading-6">
                            {pendingQuestion}
                          </div>
                        </div>
                      ) : null}
                      {sending ? (
                        <div className="flex items-center gap-2 text-sm text-muted-foreground">
                          <span className="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
                            <Bot className="size-3.5" />
                          </span>
                          <span className="flex items-center gap-2">
                            <LoaderCircle className="size-3.5 animate-spin" />
                            Checking the evidence
                          </span>
                        </div>
                      ) : null}
                    </div>
                  )}
                  <div ref={transcriptEndRef} />
                </div>
              </div>

              <div className="shrink-0 px-4 pb-4 sm:px-6 sm:pb-6">
                <div className="mx-auto w-full max-w-3xl">
                  {error ? (
                    <Alert className="mb-3" variant="destructive">
                      <AlertDescription>{error}</AlertDescription>
                    </Alert>
                  ) : null}
                  <form
                    className="flex items-end gap-2 rounded-[1.6rem] border bg-card p-2 pl-4 shadow-lg shadow-foreground/5 focus-within:ring-2 focus-within:ring-ring/40"
                    onSubmit={(event) => {
                      event.preventDefault()
                      void submitQuestion()
                    }}
                  >
                    <textarea
                      aria-label="Ask about the evidence"
                      className="max-h-36 min-h-10 min-w-0 flex-1 resize-none bg-transparent py-2 text-sm leading-6 outline-none placeholder:text-muted-foreground sm:text-base"
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
                          ? "Ask about this document"
                          : "Ask across current evidence"
                      }
                      ref={composerRef}
                      rows={1}
                      value={question}
                    />
                    <span className="hidden items-center gap-1.5 pb-2 text-[0.68rem] whitespace-nowrap text-muted-foreground sm:flex">
                      <ShieldCheck className="size-3.5" /> Evidence memory on
                    </span>
                    <Button
                      aria-label="Send question"
                      className="rounded-full"
                      disabled={!question.trim() || sending}
                      size="icon-lg"
                      type="submit"
                    >
                      {sending ? (
                        <LoaderCircle className="animate-spin" />
                      ) : (
                        <Send />
                      )}
                    </Button>
                  </form>
                  <p className="mt-2 text-center text-[0.65rem] leading-4 text-muted-foreground">
                    Verify cited evidence. Responses are not legal advice.
                  </p>
                </div>
              </div>
            </div>
          </section>
        </BodyPortal>
      ) : null}
    </>
  )
}
