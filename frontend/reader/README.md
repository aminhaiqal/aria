# ARIA reader frontend

This workspace contains ARIA's self-hosted React and TypeScript reader. Vite builds repository-owned
shadcn components into hashed assets that Django serves from `/reader/assets/app/`. Django remains
responsible for sessions, CSRF, API permissions, search bounds, and evidence integrity.

Node is required only for development and image builds. Production runs no Node service.

## Validate

From the repository root:

```bash
make frontend-test
make reader-e2e
```

The first command installs from `package-lock.json`, lints, runs Vitest and axe-core with coverage
floors, type-checks, and creates a production bundle. The second exercises Django login, the React
mount, a shadcn control, CSP errors, and logout in Chromium.

## Adding components

To add components to your app, run the following command:

```bash
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD/frontend/reader:/workspace" -w /workspace node:22-bookworm-slim \
  npx shadcn@4.16.2 add button
```

This will place the ui components in the `src/components` directory.

## Using components

To use the components in your app, import them as follows:

```tsx
import { Button } from "@/components/ui/button"
```
