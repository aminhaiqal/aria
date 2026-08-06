import react from "@vitejs/plugin-react"
import { defineConfig } from "vitest/config"

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": `${import.meta.dirname}/src`,
    },
  },
  test: {
    coverage: {
      exclude: ["src/components/ui/**", "src/main.tsx", "src/test/**"],
      provider: "v8",
      reporter: ["text", "json-summary"],
      thresholds: {
        branches: 40,
        functions: 55,
        lines: 55,
        statements: 53,
      },
    },
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
})
