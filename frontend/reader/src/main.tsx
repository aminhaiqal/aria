import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import App from "./App.tsx"
import { ThemeProvider } from "@/components/theme-provider.tsx"
import { TooltipProvider } from "@/components/ui/tooltip.tsx"

const root = document.getElementById("aria-reader-root")
if (!root) throw new Error("ARIA reader root element is unavailable.")

createRoot(root).render(
  <StrictMode>
    <ThemeProvider
      defaultTheme="light"
      disableTransitionOnChange={false}
      storageKey="aria-reader-theme"
    >
      <TooltipProvider delayDuration={250}>
        <App />
      </TooltipProvider>
    </ThemeProvider>
  </StrictMode>
)
