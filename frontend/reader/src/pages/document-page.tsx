import { useEffect, useState } from "react"
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRightLeft,
  Bot,
  Building2,
  CheckCircle2,
  Download,
  ExternalLink,
  FileCheck2,
  Fingerprint,
  History,
  Link as LinkIcon,
  Scale,
  ShieldCheck,
  Sparkles,
  Target,
} from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { fetchDocument } from "@/lib/api"
import {
  formatBytes,
  formatDate,
  safeExternalUrl,
  shortHash,
  titleCase,
} from "@/lib/format"
import type { DocumentPayload, ReaderBootstrap } from "@/lib/types"

function DocumentSkeleton() {
  return (
    <div
      className="mx-auto grid max-w-6xl gap-6 px-4 py-10 sm:px-6 lg:px-8"
      aria-label="Loading document"
      role="status"
    >
      <Skeleton className="h-5 w-48" />
      <Skeleton className="h-12 w-4/5" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-96 w-full" />
    </div>
  )
}

function GptSummary({
  summary,
}: {
  summary: NonNullable<DocumentPayload["gpt_summary"]>
}) {
  const { output } = summary
  return (
    <Card className="border-primary/25 bg-primary/4">
      <CardHeader className="gap-3 border-b border-primary/15">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Badge>
            <Sparkles data-icon="inline-start" /> GPT-generated change summary
          </Badge>
          <span className="text-xs text-muted-foreground">
            {summary.model} · {summary.prompt_version}
          </span>
        </div>
        <CardTitle className="text-xl">
          {output.title || "Reviewed textual changes"}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 leading-7">
        {output.overview ? <p>{output.overview}</p> : null}
        {output.changes?.length ? (
          <ul className="grid gap-3">
            {output.changes.map((change, index) => (
              <li
                className="rounded-lg border bg-background/70 p-3"
                key={`${change.change_type}-${index}`}
              >
                <strong>{titleCase(change.change_type || "change")}</strong>
                {change.explanation ? (
                  <span> — {change.explanation}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : null}
        {output.caveats?.length ? (
          <div className="rounded-lg border border-amber-500/25 bg-amber-500/8 p-4">
            <strong className="text-sm">Caveats</strong>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {output.caveats.map((caveat) => (
                <li key={caveat}>{caveat}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="flex items-start gap-2 border-t pt-4 text-xs text-muted-foreground">
          <Scale className="mt-0.5 size-4 shrink-0" /> This summary uses only
          human-confirmed textual changes. Legal effect was not assessed.
        </p>
      </CardContent>
    </Card>
  )
}

function ReviewedImpactPanel({ document }: { document: DocumentPayload }) {
  if (document.reviewed_impacts.length === 0) return null
  const profile = document.selected_profile
  return (
    <section className="mb-8 overflow-hidden rounded-2xl border border-primary/25 bg-primary/4">
      <header className="grid gap-3 border-b border-primary/15 px-5 py-5 sm:px-7">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Badge>
            <Building2 data-icon="inline-start" />
            {profile
              ? `Why this matters to ${profile.name}`
              : "Human-reviewed business impacts"}
          </Badge>
          <span className="text-xs text-muted-foreground">
            {document.reviewed_impacts.length} reviewed{" "}
            {document.reviewed_impacts.length === 1 ? "impact" : "impacts"}
          </span>
        </div>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          {profile
            ? "ARIA matched this document using exact controlled terms from your profile and the human review."
            : "These candidate impacts passed human review and remain linked to the exact source change."}
        </p>
      </header>
      <div className="grid gap-6 p-5 sm:p-7">
        {document.reviewed_impacts.map((impact) => (
          <article className="grid gap-5" key={impact.review_id}>
            <div className="grid gap-3 lg:grid-cols-[1fr_auto] lg:items-start">
              <div>
                <div className="mb-2 flex flex-wrap gap-2">
                  <Badge variant="secondary">
                    {titleCase(impact.impact_type)}
                  </Badge>
                  <Badge variant="outline">
                    {titleCase(impact.review_decision)}
                  </Badge>
                </div>
                <h2 className="text-xl font-semibold sm:text-2xl">
                  {impact.reviewed_title}
                </h2>
                <p className="mt-2 max-w-3xl leading-7">
                  {impact.reviewed_statement}
                </p>
              </div>
              {impact.reviewed_effective_date_text ? (
                <div className="rounded-lg border bg-background px-3 py-2 text-sm">
                  <span className="block text-xs text-muted-foreground">
                    Stated effective date
                  </span>
                  <strong>{impact.reviewed_effective_date_text}</strong>
                </div>
              ) : null}
            </div>

            {impact.relevance ? (
              <div className="rounded-xl border border-primary/20 bg-background/75 p-4">
                <p className="flex items-start gap-2 text-sm leading-6">
                  <Target className="mt-1 size-4 shrink-0 text-primary" />
                  <span>{impact.relevance.explanation}</span>
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {impact.relevance.matched_terms.map((term) => (
                    <Badge
                      key={`${term.dimension}-${term.code}`}
                      variant="outline"
                    >
                      {titleCase(term.dimension)} · {term.label}
                    </Badge>
                  ))}
                </div>
              </div>
            ) : null}

            <div>
              <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
                <ArrowRightLeft className="size-4 text-primary" /> Exact before
                and after evidence
              </h3>
              <div className="grid gap-3 md:grid-cols-2">
                {impact.evidence.map((evidence) => (
                  <div
                    className="rounded-xl border bg-background p-4"
                    key={evidence.side}
                  >
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <Badge
                        variant={
                          evidence.side === "after" ? "default" : "outline"
                        }
                      >
                        {titleCase(evidence.side)}
                      </Badge>
                      <span className="font-mono text-[0.65rem] text-muted-foreground">
                        {shortHash(evidence.artifact_sha256, 16)}
                      </span>
                    </div>
                    <blockquote className="text-sm leading-6 text-foreground/85">
                      {evidence.anchor_text}
                    </blockquote>
                    <p className="mt-3 border-t pt-3 font-mono text-[0.65rem] break-all text-muted-foreground">
                      Anchor {shortHash(evidence.anchor_text_sha256, 20)} ·
                      locator {JSON.stringify(evidence.source_locator)}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </article>
        ))}
        <p className="flex items-start gap-2 border-t pt-4 text-xs leading-5 text-muted-foreground">
          <Scale className="mt-0.5 size-4 shrink-0" /> Business relevance is an
          explainable screening result. Legal effect was not assessed.
        </p>
      </div>
    </section>
  )
}

function EvidenceTab({ document }: { document: DocumentPayload }) {
  return (
    <div className="grid gap-6 lg:grid-cols-[1.3fr_0.7fr]">
      <div className="grid gap-4">
        {document.evidence.map((evidence) => {
          const observedUrl = safeExternalUrl(evidence.observed_url)
          return (
            <Card key={evidence.artifact_id}>
              <CardHeader className="border-b">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <CardTitle className="flex items-center gap-2">
                    <FileCheck2 className="size-5 text-primary" />{" "}
                    {evidence.content_type}
                  </CardTitle>
                  <Badge variant="outline">Immutable artifact</Badge>
                </div>
              </CardHeader>
              <CardContent className="grid gap-5 sm:grid-cols-2">
                <dl className="grid gap-3 text-sm">
                  <div>
                    <dt className="text-xs text-muted-foreground">SHA-256</dt>
                    <dd className="mt-1 font-mono text-xs break-all">
                      {evidence.sha256}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Size</dt>
                    <dd className="mt-1">{formatBytes(evidence.byte_size)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Retrieved</dt>
                    <dd className="mt-1">
                      {formatDate(evidence.retrieved_at)}
                    </dd>
                  </div>
                </dl>
                <dl className="grid gap-3 text-sm">
                  <div>
                    <dt className="text-xs text-muted-foreground">
                      Source endpoint
                    </dt>
                    <dd className="mt-1">
                      {evidence.source_endpoint || "Not recorded"}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">Extractor</dt>
                    <dd className="mt-1">
                      {evidence.extraction.name} {evidence.extraction.version}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">
                      Extraction status
                    </dt>
                    <dd className="mt-1">
                      {titleCase(evidence.extraction.status)}
                    </dd>
                  </div>
                </dl>
                <div className="flex flex-wrap gap-2 sm:col-span-2">
                  <Button asChild>
                    <a
                      href={`/reader/artifacts/${encodeURIComponent(evidence.artifact_id)}/content/`}
                    >
                      <Download data-icon="inline-start" /> Download and verify
                    </a>
                  </Button>
                  {observedUrl ? (
                    <Button asChild variant="outline">
                      <a href={observedUrl} rel="noreferrer" target="_blank">
                        Observed official URL{" "}
                        <ExternalLink data-icon="inline-end" />
                      </a>
                    </Button>
                  ) : null}
                </div>
              </CardContent>
            </Card>
          )
        })}
        {document.evidence.length === 0 ? (
          <Alert>
            <AlertTriangle />
            <AlertTitle>No reader-eligible evidence</AlertTitle>
            <AlertDescription>
              The normalized text remains unavailable for verification.
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      <div className="grid content-start gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="size-5 text-primary" /> Extraction quality
            </CardTitle>
          </CardHeader>
          <CardContent>
            {document.quality ? (
              <>
                <div className="flex items-end justify-between gap-4">
                  <strong className="text-xl">
                    {titleCase(document.quality.outcome)}
                  </strong>
                  <span className="text-3xl font-semibold">
                    {document.quality.score}
                    <small className="text-sm text-muted-foreground">
                      /100
                    </small>
                  </span>
                </div>
                <Separator className="my-4" />
                <div className="grid gap-3">
                  {document.quality.findings.map((finding) => (
                    <div
                      className="rounded-lg bg-muted/50 p-3"
                      key={`${finding.code}-${finding.message}`}
                    >
                      <strong className="text-sm">{finding.code}</strong>
                      <p className="mt-1 text-sm text-muted-foreground">
                        {finding.message}
                      </p>
                    </div>
                  ))}
                  {document.quality.findings.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No quality findings were recorded.
                    </p>
                  ) : null}
                </div>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                No document-level quality assessment is attached to this
                version.
              </p>
            )}
          </CardContent>
        </Card>

        {document.comparison ? (
          <Card>
            <CardHeader>
              <CardTitle>Previous-version comparison</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              {Object.entries(document.comparison.counts).map(
                ([label, count]) => (
                  <div className="rounded-lg bg-muted/50 p-3" key={label}>
                    <span className="block text-xs text-muted-foreground">
                      {titleCase(label)}
                    </span>
                    <strong className="text-xl">{count}</strong>
                  </div>
                )
              )}
              <p className="col-span-2 text-xs leading-5 text-muted-foreground">
                Structural text classifications are not findings about legal
                effect.
              </p>
            </CardContent>
          </Card>
        ) : null}
      </div>
    </div>
  )
}

function VersionTab({ document }: { document: DocumentPayload }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History className="size-5 text-primary" /> {document.versions.length}{" "}
          archived {document.versions.length === 1 ? "version" : "versions"}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ol className="relative ml-3 grid gap-0 border-l">
          {document.versions.map((version) => (
            <li
              className="relative grid gap-2 py-4 pl-7 sm:grid-cols-[1fr_auto] sm:items-center"
              key={version.id}
            >
              <span
                className={`absolute top-5 -left-2 size-4 rounded-full border-4 border-background ${version.is_current ? "bg-primary" : "bg-muted-foreground"}`}
              />
              <div>
                <strong>{formatDate(version.created_at)}</strong>
                <code className="mt-1 block text-xs break-all text-muted-foreground">
                  {version.normalized_content_sha256}
                </code>
              </div>
              {version.is_current ? (
                <Badge>
                  <CheckCircle2 data-icon="inline-start" /> Current
                </Badge>
              ) : null}
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  )
}

export function DocumentPage({ bootstrap }: { bootstrap: ReaderBootstrap }) {
  const [document, setDocument] = useState<DocumentPayload | null>(null)
  const [error, setError] = useState("")

  useEffect(() => {
    const controller = new AbortController()
    fetchDocument(bootstrap, bootstrap.documentId, controller.signal)
      .then((payload) => {
        setDocument(payload)
        window.document.title = `${payload.identity.title} · ARIA`
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(
            reason instanceof Error
              ? reason.message
              : "The document could not be loaded."
          )
        }
      })
    return () => controller.abort()
  }, [bootstrap])

  if (error) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-12 sm:px-6">
        <Alert variant="destructive">
          <AlertTriangle />
          <AlertTitle>Document unavailable</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
        <Button asChild className="mt-5" variant="outline">
          <a href={bootstrap.searchUrl}>
            <ArrowLeft data-icon="inline-start" /> Return to search
          </a>
        </Button>
      </div>
    )
  }
  if (!document) return <DocumentSkeleton />

  const officialUrl = safeExternalUrl(document.identity.canonical_url)
  return (
    <>
      <section className="border-b bg-muted/25">
        <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6 sm:py-14 lg:px-8">
          <nav
            className="mb-6 flex flex-wrap items-center gap-2 text-sm text-muted-foreground"
            aria-label="Breadcrumb"
          >
            <a className="hover:text-foreground" href={bootstrap.searchUrl}>
              Search
            </a>
            <span aria-hidden="true">/</span>
            <span>{document.authority.name}</span>
          </nav>
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_18rem] lg:items-start">
            <div className="min-w-0">
              <div className="mb-4 flex flex-wrap gap-2">
                <Badge>{document.authority.name}</Badge>
                <Badge variant="outline">{document.collection.name}</Badge>
                <Badge variant="secondary">
                  {titleCase(document.authority.trust_classification)}
                </Badge>
              </div>
              <h1 className="text-3xl font-semibold tracking-tight text-balance break-words sm:text-5xl">
                {document.identity.title}
              </h1>
              <p className="mt-5 max-w-3xl text-sm leading-6 text-muted-foreground">
                Current ARIA version archived{" "}
                {formatDate(document.version.created_at)}. This timestamp
                describes ARIA&apos;s evidence record, not a legal commencement
                date.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                {officialUrl ? (
                  <Button asChild size="lg">
                    <a href={officialUrl} rel="noreferrer" target="_blank">
                      Official source <ExternalLink data-icon="inline-end" />
                    </a>
                  </Button>
                ) : null}
                {document.evidence[0] ? (
                  <Button asChild size="lg" variant="outline">
                    <a
                      href={`/reader/artifacts/${encodeURIComponent(document.evidence[0].artifact_id)}/content/`}
                    >
                      <Download data-icon="inline-start" /> Archived evidence
                    </a>
                  </Button>
                ) : null}
              </div>
            </div>
            <Card className="bg-background">
              <CardContent className="grid gap-4">
                <div className="flex justify-between">
                  <span className="text-sm text-muted-foreground">
                    Sections
                  </span>
                  <strong>{document.section_count}</strong>
                </div>
                <Separator />
                <div className="flex justify-between">
                  <span className="text-sm text-muted-foreground">
                    Versions
                  </span>
                  <strong>{document.versions.length}</strong>
                </div>
                <Separator />
                <div>
                  <span className="text-sm text-muted-foreground">
                    Content hash
                  </span>
                  <code className="mt-1 block text-xs break-all">
                    {shortHash(document.version.normalized_content_sha256, 24)}
                  </code>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-6xl px-4 py-10 sm:px-6 lg:px-8">
        <ReviewedImpactPanel document={document} />
        {document.gpt_summary ? (
          <div className="mb-8">
            <GptSummary summary={document.gpt_summary} />
          </div>
        ) : null}

        <Tabs defaultValue="text">
          <TabsList
            className="mb-6 h-auto w-full justify-start overflow-x-auto"
            variant="line"
          >
            <TabsTrigger className="px-3 py-2" value="text">
              <FileCheck2 /> Official text
            </TabsTrigger>
            <TabsTrigger className="px-3 py-2" value="evidence">
              <Fingerprint /> Evidence & quality
            </TabsTrigger>
            <TabsTrigger className="px-3 py-2" value="versions">
              <History /> Version history
            </TabsTrigger>
          </TabsList>

          <TabsContent value="text">
            <header className="mb-6">
              <p className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">
                Normalized official text
              </p>
              <h2 className="mt-1 text-2xl font-semibold">Document passages</h2>
              <p className="mt-2 text-sm text-muted-foreground">
                Every passage retains its source artifact, page, locator, and
                text hash.
              </p>
            </header>
            {document.sections_truncated ? (
              <Alert className="mb-5">
                <AlertTriangle />
                <AlertTitle>Preview limit reached</AlertTitle>
                <AlertDescription>
                  Use the archived evidence for the complete document.
                </AlertDescription>
              </Alert>
            ) : null}
            <div className="grid gap-4">
              {document.sections.map((section) => (
                <article
                  className="scroll-mt-28 rounded-xl border bg-card p-5 shadow-sm sm:p-7"
                  id={`section-${section.id}`}
                  key={section.id}
                >
                  <header className="mb-4 flex items-start justify-between gap-4 border-b pb-4">
                    <div>
                      <span className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
                        Section {section.ordinal}
                        {section.page_number
                          ? ` · page ${section.page_number}`
                          : ""}
                      </span>
                      {section.heading ? (
                        <h3 className="mt-1 text-lg font-semibold">
                          {section.heading}
                        </h3>
                      ) : null}
                    </div>
                    <Button
                      asChild
                      aria-label="Link to this passage"
                      size="icon"
                      variant="ghost"
                    >
                      <a href={`#section-${section.id}`}>
                        <LinkIcon />
                      </a>
                    </Button>
                  </header>
                  <div className="text-[0.98rem] leading-8 whitespace-pre-wrap text-foreground/90">
                    {section.text}
                  </div>
                  <footer className="mt-5 flex flex-col gap-1 border-t pt-4 font-mono text-[0.68rem] text-muted-foreground sm:flex-row sm:justify-between">
                    <span>Text {shortHash(section.text_sha256)}</span>
                    <span>Artifact {shortHash(section.artifact_id, 12)}</span>
                  </footer>
                </article>
              ))}
              {document.sections.length === 0 ? (
                <Alert>
                  <Bot />
                  <AlertTitle>No normalized sections</AlertTitle>
                  <AlertDescription>
                    The document remains available through its archived
                    evidence.
                  </AlertDescription>
                </Alert>
              ) : null}
            </div>
          </TabsContent>

          <TabsContent value="evidence">
            <EvidenceTab document={document} />
          </TabsContent>
          <TabsContent value="versions">
            <VersionTab document={document} />
          </TabsContent>
        </Tabs>
      </div>
    </>
  )
}
