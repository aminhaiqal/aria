import axe from "axe-core"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
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
      data-profiles-api="/api/reader/v1/profiles/"
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
  expect(screen.getAllByText(/Legal effect was not assessed/)).toHaveLength(2)
  expect(
    screen.getByText(/Why this matters to Malaysia data team/)
  ).toBeInTheDocument()
  expect(
    screen.getByText("Technical safeguards may need review")
  ).toBeInTheDocument()
  expect(
    screen.getByText(/Exact before and after evidence/)
  ).toBeInTheDocument()
  expect(
    screen.getByRole("link", { name: /Archived evidence/ })
  ).toHaveAttribute(
    "href",
    `/reader/artifacts/${documentResponse.evidence[0].artifact_id}/content/`
  )
  expect((await axe.run(container)).violations).toHaveLength(0)
})

test("creates a controlled business profile through the same-origin API", async () => {
  window.history.replaceState({}, "", "/reader/")
  bootstrap()
  const createdProfile = {
    ...readerOptions.business_profiles[0],
    id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    name: "New data team",
  }
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((input, init) => {
      const url = String(input)
      if (url.includes("options")) return jsonResponse(readerOptions)
      if (url.includes("profiles") && init?.method === "POST") {
        return jsonResponse({
          profile: createdProfile,
          evaluation: { evaluated: 1, matched: 1 },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    })
  const user = userEvent.setup()

  renderApp()
  await user.click(
    await screen.findByRole("button", { name: "Set up business profile" })
  )
  await user.type(screen.getByLabelText("Profile name"), createdProfile.name)
  await user.click(screen.getByRole("checkbox", { name: /Data user/ }))
  await user.click(
    screen.getByRole("button", { name: "Save and evaluate profile" })
  )

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  const [, request] = fetchMock.mock.calls[1]
  expect(request?.headers).toMatchObject({ "X-CSRFToken": "test-csrf" })
  expect(JSON.parse(String(request?.body))).toEqual({
    name: createdProfile.name,
    taxonomy_id: readerOptions.applicability_taxonomy?.id,
    term_ids: [readerOptions.applicability_taxonomy?.terms[0].id],
  })
  expect(
    screen.queryByText("Describe the organization")
  ).not.toBeInTheDocument()
})

test("labels an exact profile match in search results", async () => {
  const profile = readerOptions.business_profiles[0]
  window.history.replaceState(
    {},
    "",
    `/reader/?q=protect+personal+data&mode=full_text&profile=${profile.id}`
  )
  bootstrap()
  const relevantSearch = {
    ...searchResponse,
    filters: { ...searchResponse.filters, profile: profile.id },
    results: searchResponse.results.map((result) => ({
      ...result,
      relevance: {
        profile_id: profile.id,
        profile_name: profile.name,
        impact_count: 1,
        impacts: documentResponse.reviewed_impacts,
      },
    })),
  }
  vi.spyOn(globalThis, "fetch").mockImplementation((input) =>
    String(input).includes("options")
      ? jsonResponse(readerOptions)
      : jsonResponse(relevantSearch)
  )

  renderApp()

  expect(
    await screen.findByText(`Matched to ${profile.name}`)
  ).toBeInTheDocument()
  expect(
    screen.getByText("Technical safeguards may need review")
  ).toBeInTheDocument()
  expect(
    screen.getByRole("link", { name: "Official Data Processing Guidance" })
  ).toHaveAttribute("href", expect.stringContaining(`profile=${profile.id}`))
})
