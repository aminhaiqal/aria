import {
  formatBytes,
  safeExternalUrl,
  shortHash,
  titleCase,
} from "@/lib/format"
import { expect, test } from "vitest"

test("formats reader evidence metadata", () => {
  expect(formatBytes(4096)).toBe("4.00 KB")
  expect(shortHash("a".repeat(64))).toBe(`${"a".repeat(16)}…`)
  expect(titleCase("full_text")).toBe("Full Text")
})

test("only allows HTTP and HTTPS external links", () => {
  expect(safeExternalUrl("https://example.test/evidence.pdf")).toBe(
    "https://example.test/evidence.pdf"
  )
  expect(safeExternalUrl("javascript:alert(1)")).toBeNull()
  expect(safeExternalUrl("not a URL")).toBeNull()
})
