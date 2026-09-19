import type { ReactNode } from "react"
import {
  BookOpenCheck,
  ExternalLink,
  LogOut,
  Moon,
  Search,
  ShieldCheck,
  Sun,
  TerminalSquare,
} from "lucide-react"

import { useTheme } from "@/components/theme-provider"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { ReaderBootstrap } from "@/lib/types"
import { ChatAssistant } from "@/components/chat-assistant"

type ReaderShellProps = {
  bootstrap: ReaderBootstrap
  children: ReactNode
}

export function ReaderShell({ bootstrap, children }: ReaderShellProps) {
  const { theme, setTheme } = useTheme()
  const nextTheme = theme === "dark" ? "light" : "dark"

  return (
    <div className="min-h-svh bg-background text-foreground">
      <a
        className="sr-only z-50 rounded-md bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-4 focus:left-4"
        href="#reader-content"
      >
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-border/70 bg-background/92 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[90rem] items-center gap-4 px-4 sm:px-6 lg:px-8">
          <a
            className="group flex min-w-0 items-center gap-3"
            href={bootstrap.searchUrl}
          >
            <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-primary text-sm font-semibold text-primary-foreground shadow-sm transition-transform group-hover:-rotate-3">
              A
            </span>
            <span className="min-w-0 leading-tight">
              <strong className="block tracking-[0.14em]">ARIA</strong>
              <small className="hidden truncate text-muted-foreground sm:block">
                Official evidence reader
              </small>
            </span>
          </a>

          <nav
            className="ml-auto flex items-center gap-1"
            aria-label="Reader navigation"
          >
            <ChatAssistant bootstrap={bootstrap} />
            <Button asChild variant="ghost" className="hidden sm:inline-flex">
              <a href={bootstrap.searchUrl}>
                <Search data-icon="inline-start" /> Search
              </a>
            </Button>
            <Button asChild variant="ghost" className="hidden md:inline-flex">
              <a href={`${bootstrap.searchApi}?q=personal+data&mode=full_text`}>
                <TerminalSquare data-icon="inline-start" /> API
              </a>
            </Button>
            {bootstrap.userStaff ? (
              <Button asChild variant="ghost" className="hidden lg:inline-flex">
                <a href="/console/">
                  <ShieldCheck data-icon="inline-start" /> Console
                </a>
              </Button>
            ) : null}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-label={`Switch to ${nextTheme} theme`}
                  onClick={() => setTheme(nextTheme)}
                  size="icon"
                  type="button"
                  variant="ghost"
                >
                  {theme === "dark" ? <Sun /> : <Moon />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>Switch to {nextTheme} theme</TooltipContent>
            </Tooltip>
          </nav>

          <div className="hidden h-8 items-center gap-3 border-l pl-4 sm:flex">
            <span className="max-w-40 truncate text-xs text-muted-foreground">
              {bootstrap.userName}
            </span>
            <form action={bootstrap.logoutUrl} method="post">
              <input
                name="csrfmiddlewaretoken"
                type="hidden"
                value={bootstrap.csrfToken}
              />
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    aria-label="Sign out"
                    size="icon"
                    type="submit"
                    variant="ghost"
                  >
                    <LogOut />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Sign out</TooltipContent>
              </Tooltip>
            </form>
          </div>
        </div>
      </header>

      <main id="reader-content">{children}</main>

      <footer className="mt-16 border-t bg-muted/30">
        <div className="mx-auto flex max-w-[90rem] flex-col gap-5 px-4 py-8 text-sm text-muted-foreground sm:px-6 md:flex-row md:items-center md:justify-between lg:px-8">
          <div className="flex items-center gap-3">
            <BookOpenCheck className="size-5 text-primary" aria-hidden="true" />
            <span>ARIA · Self-hosted official evidence interface</span>
          </div>
          <div className="flex flex-col gap-1 text-xs md:text-right">
            <span>
              Search ranking and generated summaries are not legal advice.
            </span>
            <a
              className="inline-flex items-center gap-1 hover:text-foreground md:justify-end"
              href="https://github.com/aminhaiqal/aria"
              rel="noreferrer"
              target="_blank"
            >
              Source code <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          </div>
        </div>
      </footer>
    </div>
  )
}
