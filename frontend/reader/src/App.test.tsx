import axe from "axe-core"
import { render, screen } from "@testing-library/react"
import { expect, test, vi } from "vitest"
import { ThemeProvider } from "@/components/theme-provider"
import { TooltipProvider } from "@/components/ui/tooltip"
import App from "@/App"
import {
  documentResponse,
  readerOptions,
  searchResponse,
} from "@/test/fixtures"

function bootstrap(documentId = "") {
  document.head.innerHTML = '<meta name="csrf-token" content="test-csrf">'
  document.body.innerHTML = `
    <div
      id="aria-reader-root"
      data-document-id="${documentId}"
      data-login-url="/reader/login/"
      data-logout-url="/reader/logout/"
      data-options-api="/api/reader/v1/options/"
      data-search-api="/api/reader/v1/search/"
      data-search-url="/reader/"
      data-user-name="Reader"
      data-user-staff="false"
    ></div>
  `
}

function jsonResponse(payload: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      headers: { "Content-Type": "application/json" },
      status: 200,
    })
  )
}

function renderApp() {
  return render(
    <ThemeProvider defaultTheme="light">
      <TooltipProvider>
        <App />
      </TooltipProvider>
    </ThemeProvider>
  )
}

test("renders traceable search results without critical accessibility violations", async () => {
  window.history.replaceState(
    {},
    "",
    "/reader/?q=protect+personal+data&mode=hybrid"
  )
  bootstrap()
  vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input)
    return url.includes("options")
      ? jsonResponse(readerOptions)
      : jsonResponse(searchResponse)
  })

  const { container } = renderApp()

  expect(
    await screen.findByRole("heading", {
      name: "Official Data Processing Guidance",
    })
  ).toBeInTheDocument()
  expect(
    screen.getByText(/Organizations must protect personal data/)
  ).toBeInTheDocument()
  expect(
    screen.getByText(/This query vector was created locally/)
  ).toBeInTheDocument()
  expect((await axe.run(container)).violations).toHaveLength(0)
})

test("renders the evidence-backed document and labelled GPT boundary", async () => {
  const identityId = documentResponse.identity.id
  window.history.replaceState({}, "", `/reader/documents/${identityId}/`)
  bootstrap(identityId)
  vi.spyOn(globalThis, "fetch").mockImplementation(() =>
    jsonResponse(documentResponse)
  )

  const { container } = renderApp()

  expect(
    await screen.findByRole("heading", {
      name: documentResponse.identity.title,
    })
  ).toBeInTheDocument()
  expect(screen.getByText("GPT-generated change summary")).toBeInTheDocument()
  expect(screen.getByText(/Legal effect was not assessed/)).toBeInTheDocument()
  expect(
    screen.getByRole("link", { name: /Archived evidence/ })
  ).toHaveAttribute(
    "href",
    `/reader/artifacts/${documentResponse.evidence[0].artifact_id}/content/`
  )
  expect((await axe.run(container)).violations).toHaveLength(0)
})
