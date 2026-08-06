import { useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ExternalLink,
  FileSearch,
  Filter,
  Fingerprint,
  Landmark,
  Search,
  Server,
  Sparkles,
} from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchReaderOptions, ReaderApiError, searchReader } from "@/lib/api"
import {
  formatDate,
  safeExternalUrl,
  shortHash,
  titleCase,
  withSearchPage,
} from "@/lib/format"
import type {
  ReaderBootstrap,
  ReaderOptions,
  SearchDocument,
  SearchResponse,
} from "@/lib/types"

const ALL = "__all__"

type SearchFields = {
  authority: string
  collection: string
  dateFrom: string
  dateTo: string
  mode: "hybrid" | "full_text" | "vector"
  provider: "local_hash" | "openai"
  query: string
}

function fieldsFromParameters(parameters: URLSearchParams): SearchFields {
  const mode = parameters.get("mode")
  const provider = parameters.get("embedding_provider")
  return {
    authority: parameters.get("authority") || ALL,
    collection: parameters.get("collection") || ALL,
    dateFrom: parameters.get("date_from") || "",
    dateTo: parameters.get("date_to") || "",
    mode: mode === "full_text" || mode === "vector" ? mode : "hybrid",
    provider: provider === "openai" ? "openai" : "local_hash",
    query: parameters.get("q") || "",
  }
}

function buildParameters(fields: SearchFields) {
  const parameters = new URLSearchParams({
    q: fields.query.trim(),
    mode: fields.mode,
  })
  if (fields.mode !== "full_text")
    parameters.set("embedding_provider", fields.provider)
  if (fields.authority !== ALL) parameters.set("authority", fields.authority)
  if (fields.collection !== ALL) parameters.set("collection", fields.collection)
  if (fields.dateFrom) parameters.set("date_from", fields.dateFrom)
  if (fields.dateTo) parameters.set("date_to", fields.dateTo)
  parameters.set("page", "1")
  parameters.set("page_size", "10")
  return parameters
}

function SearchSkeleton() {
  return (
    <div
      className="grid gap-4"
      aria-label="Loading search results"
      role="status"
    >
      {[0, 1, 2].map((item) => (
        <Card key={item}>
          <CardHeader className="gap-3">
            <Skeleton className="h-4 w-36" />
            <Skeleton className="h-7 w-3/4" />
          </CardHeader>
          <CardContent className="space-y-3">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

function SearchResultCard({
  bootstrap,
  document,
}: {
  bootstrap: ReaderBootstrap
  document: SearchDocument
}) {
  const officialUrl = safeExternalUrl(document.canonical_url)
  const detailUrl = `${bootstrap.searchUrl}documents/${encodeURIComponent(document.identity_id)}/`
  return (
    <Card className="transition-all hover:-translate-y-0.5 hover:shadow-lg hover:shadow-primary/5">
      <CardHeader className="gap-4 border-b">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <Badge variant="secondary">{document.authority.name}</Badge>
              <Badge variant="outline">
                {titleCase(document.authority.trust_classification)}
              </Badge>
            </div>
            <CardTitle className="text-xl sm:text-2xl">
              <h3>
                <a
                  className="decoration-primary/40 underline-offset-4 hover:underline"
                  href={detailUrl}
                >
                  {document.title}
                </a>
              </h3>
            </CardTitle>
            <p className="mt-2 text-sm text-muted-foreground">
              {document.collection.name} · archived{" "}
              {formatDate(document.version_created_at)}
            </p>
          </div>
          <span className="shrink-0 rounded-lg bg-primary/7 px-2.5 py-1 font-mono text-xs text-primary">
            score {document.score.toFixed(4)}
          </span>
        </div>
      </CardHeader>
      <CardContent className="grid gap-3">
        {document.passages.map((passage) => (
          <a
            className="group rounded-xl border bg-muted/25 p-4 transition-colors hover:border-primary/30 hover:bg-primary/4"
            href={`${detailUrl}#section-${passage.section_id}`}
            key={passage.section_id}
          >
            <span className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs font-medium text-muted-foreground">
              <span>
                {passage.heading || `Passage ${passage.ordinal}`}
                {passage.page_number ? ` · page ${passage.page_number}` : ""}
              </span>
              <span className="inline-flex items-center gap-1 font-mono">
                <Fingerprint className="size-3" aria-hidden="true" />
                {shortHash(passage.artifact.sha256)}
              </span>
            </span>
            <span className="line-clamp-4 text-sm leading-6 text-foreground/85 group-hover:text-foreground">
              {passage.excerpt}
            </span>
          </a>
        ))}
      </CardContent>
      <CardFooter className="flex flex-wrap justify-between gap-2">
        <Button asChild variant="link" className="px-0">
          <a href={detailUrl}>
            Inspect evidence <ArrowRight data-icon="inline-end" />
          </a>
        </Button>
        {officialUrl ? (
          <Button asChild variant="ghost">
            <a href={officialUrl} rel="noreferrer" target="_blank">
              Official source <ExternalLink data-icon="inline-end" />
            </a>
          </Button>
        ) : null}
      </CardFooter>
    </Card>
  )
}

export function SearchPage({ bootstrap }: { bootstrap: ReaderBootstrap }) {
  const [parameters, setParameters] = useState(
    () => new URLSearchParams(window.location.search)
  )
  const [fields, setFields] = useState(() => fieldsFromParameters(parameters))
  const [options, setOptions] = useState<ReaderOptions>({
    authorities: [],
    collections: [],
  })
  const [result, setResult] = useState<SearchResponse | null>(null)
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const hasQuery = Boolean(parameters.get("q")?.trim())

  useEffect(() => {
    const controller = new AbortController()
    fetchReaderOptions(bootstrap, controller.signal)
      .then(setOptions)
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(
            reason instanceof Error
              ? reason.message
              : "Source filters are unavailable."
          )
        }
      })
    return () => controller.abort()
  }, [bootstrap])

  useEffect(() => {
    const onPopState = () => {
      const next = new URLSearchParams(window.location.search)
      setParameters(next)
      setFields(fieldsFromParameters(next))
    }
    window.addEventListener("popstate", onPopState)
    return () => window.removeEventListener("popstate", onPopState)
  }, [])

  useEffect(() => {
    if (!hasQuery) {
      return undefined
    }
    const controller = new AbortController()
    async function loadSearch() {
      setLoading(true)
      setError("")
      try {
        setResult(await searchReader(bootstrap, parameters, controller.signal))
      } catch (reason: unknown) {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setResult(null)
          setError(
            reason instanceof ReaderApiError || reason instanceof Error
              ? reason.message
              : "Search is temporarily unavailable."
          )
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    void loadSearch()
    return () => controller.abort()
  }, [bootstrap, hasQuery, parameters])

  const filteredCollections = useMemo(
    () =>
      options.collections.filter(
        (collection) =>
          fields.authority === ALL ||
          collection.authority_slug === fields.authority
      ),
    [fields.authority, options.collections]
  )

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!fields.query.trim()) {
      setError("Enter a search query.")
      return
    }
    const next = buildParameters(fields)
    window.history.pushState(
      {},
      "",
      `${bootstrap.searchUrl}?${next.toString()}`
    )
    setParameters(next)
  }

  const page = Number(parameters.get("page") || "1")

  return (
    <>
      <section className="relative overflow-hidden border-b bg-[radial-gradient(circle_at_top_left,color-mix(in_oklch,var(--primary)_10%,transparent),transparent_42%)]">
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,color-mix(in_oklch,var(--border)_45%,transparent)_1px,transparent_1px),linear-gradient(to_bottom,color-mix(in_oklch,var(--border)_45%,transparent)_1px,transparent_1px)] [mask-image:linear-gradient(to_bottom,black,transparent_80%)] bg-[size:48px_48px]" />
        <div className="relative mx-auto max-w-5xl px-4 py-14 text-center sm:px-6 sm:py-20 lg:px-8">
          <Badge className="mb-5" variant="outline">
            <Landmark data-icon="inline-start" /> Malaysian official material
          </Badge>
          <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-6xl">
            Find the passage.{" "}
            <span className="text-primary">Verify the evidence.</span>
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-base leading-7 text-pretty text-muted-foreground sm:text-lg">
            Search current archived material from JPDP, the Attorney
            General&apos;s Chambers, and Parliament of Malaysia.
          </p>

          <form
            className="mx-auto mt-9 max-w-4xl text-left"
            onSubmit={submitSearch}
            role="search"
          >
            <div className="flex flex-col gap-2 rounded-2xl border bg-background/95 p-2 shadow-2xl shadow-primary/8 sm:flex-row">
              <Label className="sr-only" htmlFor="reader-query">
                Search official material
              </Label>
              <Input
                autoComplete="off"
                className="h-12 flex-1 border-0 bg-transparent px-4 text-base shadow-none focus-visible:ring-0"
                id="reader-query"
                maxLength={500}
                onChange={(event) =>
                  setFields((current) => ({
                    ...current,
                    query: event.target.value,
                  }))
                }
                placeholder="Search an Act, circular, bill, or exact passage…"
                value={fields.query}
              />
              <Button
                className="h-12 px-5"
                disabled={loading}
                size="lg"
                type="submit"
              >
                <Search data-icon="inline-start" />{" "}
                {loading ? "Searching…" : "Search evidence"}
              </Button>
            </div>

            <Collapsible
              className="mt-3 rounded-xl border bg-background/75 text-sm backdrop-blur"
              defaultOpen={Boolean(
                fields.authority !== ALL ||
                fields.collection !== ALL ||
                fields.dateFrom ||
                fields.dateTo
              )}
            >
              <CollapsibleTrigger asChild>
                <Button
                  className="w-full justify-between px-4"
                  type="button"
                  variant="ghost"
                >
                  <span className="inline-flex items-center gap-2">
                    <Filter className="size-4" /> Search options
                  </span>
                  <ChevronDown className="size-4 transition-transform [[data-state=open]_&]:rotate-180" />
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="grid gap-4 border-t p-4 sm:grid-cols-2 lg:grid-cols-3">
                <div className="grid gap-2">
                  <Label htmlFor="search-mode">Method</Label>
                  <Select
                    onValueChange={(value) =>
                      setFields((current) => ({
                        ...current,
                        mode: value as SearchFields["mode"],
                      }))
                    }
                    value={fields.mode}
                  >
                    <SelectTrigger className="w-full" id="search-mode">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="hybrid">Hybrid ranking</SelectItem>
                      <SelectItem value="full_text">Exact text</SelectItem>
                      <SelectItem value="vector">Semantic only</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="embedding-provider">Semantic provider</Label>
                  <Select
                    disabled={fields.mode === "full_text"}
                    onValueChange={(value) =>
                      setFields((current) => ({
                        ...current,
                        provider: value as SearchFields["provider"],
                      }))
                    }
                    value={fields.provider}
                  >
                    <SelectTrigger className="w-full" id="embedding-provider">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="local_hash">Local hash</SelectItem>
                      <SelectItem value="openai">OpenAI</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="authority">Authority</Label>
                  <Select
                    onValueChange={(value) =>
                      setFields((current) => ({
                        ...current,
                        authority: value,
                        collection: ALL,
                      }))
                    }
                    value={fields.authority}
                  >
                    <SelectTrigger className="w-full" id="authority">
                      <SelectValue placeholder="All authorities" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ALL}>All authorities</SelectItem>
                      {options.authorities.map((authority) => (
                        <SelectItem key={authority.id} value={authority.slug}>
                          {authority.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="collection">Collection</Label>
                  <Select
                    onValueChange={(value) =>
                      setFields((current) => ({
                        ...current,
                        collection: value,
                      }))
                    }
                    value={fields.collection}
                  >
                    <SelectTrigger className="w-full" id="collection">
                      <SelectValue placeholder="All collections" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={ALL}>All collections</SelectItem>
                      {filteredCollections.map((collection) => (
                        <SelectItem key={collection.id} value={collection.id}>
                          {collection.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="date-from">ARIA version from</Label>
                  <Input
                    id="date-from"
                    onChange={(event) =>
                      setFields((current) => ({
                        ...current,
                        dateFrom: event.target.value,
                      }))
                    }
                    type="date"
                    value={fields.dateFrom}
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="date-to">ARIA version to</Label>
                  <Input
                    id="date-to"
                    onChange={(event) =>
                      setFields((current) => ({
                        ...current,
                        dateTo: event.target.value,
                      }))
                    }
                    type="date"
                    value={fields.dateTo}
                  />
                </div>
              </CollapsibleContent>
            </Collapsible>
          </form>
          <p className="mx-auto mt-4 max-w-2xl text-xs leading-5 text-muted-foreground">
            ARIA ranks textual similarity. It does not determine legal
            relevance, legal effect, or applicability.
          </p>
        </div>
      </section>

      <section
        className="mx-auto max-w-6xl px-4 py-10 sm:px-6 lg:px-8"
        aria-live="polite"
      >
        {error ? (
          <Alert variant="destructive" className="mb-6">
            <FileSearch />
            <AlertTitle>Reader request could not be completed</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}

        {loading ? <SearchSkeleton /> : null}

        {!loading && hasQuery && result ? (
          <div className="grid gap-6">
            <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <p className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">
                  Bounded retrieval
                </p>
                <h2 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">
                  {result.bounded_result_count}{" "}
                  {result.bounded_result_count === 1 ? "document" : "documents"}{" "}
                  matched
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Page {result.page} · {titleCase(result.mode)} ranking
                </p>
              </div>
              {result.embedding ? (
                <div className="flex max-w-md items-start gap-3 rounded-xl border bg-muted/35 px-4 py-3 text-xs">
                  {result.embedding.query_sent_to_provider ? (
                    <Sparkles className="mt-0.5 size-4 shrink-0 text-primary" />
                  ) : (
                    <Server className="mt-0.5 size-4 shrink-0 text-primary" />
                  )}
                  <span>
                    <strong className="block text-foreground">
                      {result.embedding.provider} · {result.embedding.model}
                    </strong>
                    {result.embedding.query_sent_to_provider
                      ? "This query was sent to OpenAI to create its vector."
                      : "This query vector was created locally."}
                  </span>
                </div>
              ) : null}
            </header>

            {result.warnings.map((warning) => (
              <Alert key={warning}>
                <AlertTitle>Search degraded visibly</AlertTitle>
                <AlertDescription>{warning}</AlertDescription>
              </Alert>
            ))}

            <div className="grid gap-5">
              {result.results.map((document) => (
                <SearchResultCard
                  bootstrap={bootstrap}
                  document={document}
                  key={document.identity_id}
                />
              ))}
            </div>

            {result.results.length === 0 ? (
              <Card className="py-14 text-center">
                <CardContent>
                  <FileSearch className="mx-auto mb-4 size-9 text-muted-foreground" />
                  <h3 className="text-lg font-medium">
                    No matching evidence found
                  </h3>
                  <p className="mt-2 text-muted-foreground">
                    Try fewer words, exact-text mode, or remove a source filter.
                  </p>
                </CardContent>
              </Card>
            ) : null}

            {result.has_previous || result.has_next ? (
              <nav
                className="flex items-center justify-between border-t pt-6"
                aria-label="Search result pages"
              >
                {result.has_previous ? (
                  <Button asChild variant="outline">
                    <a href={withSearchPage(parameters, page - 1)}>
                      <ArrowLeft data-icon="inline-start" /> Previous
                    </a>
                  </Button>
                ) : (
                  <span />
                )}
                <span className="text-sm text-muted-foreground">
                  Page {result.page}
                </span>
                {result.has_next ? (
                  <Button asChild variant="outline">
                    <a href={withSearchPage(parameters, page + 1)}>
                      Next <ArrowRight data-icon="inline-end" />
                    </a>
                  </Button>
                ) : (
                  <span />
                )}
              </nav>
            ) : null}
          </div>
        ) : null}

        {!loading && !hasQuery ? (
          <div className="grid gap-4 md:grid-cols-3">
            {[
              [
                "JPDP",
                "Personal data protection",
                "Official circulars and regulator publications.",
              ],
              [
                "AGC",
                "Federal legislation",
                "Current principal Acts from the Attorney General's Chambers.",
              ],
              [
                "Parliament",
                "Legislative material",
                "Dewan Rakyat bills and their archived evidence.",
              ],
            ].map(([source, title, description]) => (
              <Card key={source}>
                <CardHeader>
                  <Badge className="mb-3 w-fit" variant="outline">
                    {source}
                  </Badge>
                  <CardTitle>{title}</CardTitle>
                </CardHeader>
                <CardContent className="text-muted-foreground">
                  {description}
                </CardContent>
              </Card>
            ))}
          </div>
        ) : null}
      </section>
    </>
  )
}
