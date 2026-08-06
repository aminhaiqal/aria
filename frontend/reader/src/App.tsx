import { useState } from "react"

import { ReaderShell } from "@/components/reader-shell"
import { readBootstrap } from "@/lib/api"
import { DocumentPage } from "@/pages/document-page"
import { SearchPage } from "@/pages/search-page"

export function App() {
  const [bootstrap] = useState(readBootstrap)
  return (
    <ReaderShell bootstrap={bootstrap}>
      {bootstrap.documentId ? (
        <DocumentPage bootstrap={bootstrap} />
      ) : (
        <SearchPage bootstrap={bootstrap} />
      )}
    </ReaderShell>
  )
}

export default App
