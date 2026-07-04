# Frontend

React SPA for Auto CRUD. Reads the backend's metadata endpoints to discover the live database schema and builds its UI dynamically — no frontend changes required when tables or columns are added.

## Tech stack

| Package | Version | Purpose |
|---|---|---|
| React | 19 | UI framework |
| TypeScript | 6 | Type safety |
| Vite | 8 | Dev server, bundler, build tool |
| Tailwind CSS | 4 | Utility-first styling |
| shadcn/ui | Base Nova | Accessible, unstyled-first component library |
| Base UI | — | Headless primitives underlying the Nova preset |
| TanStack Query | 5 | Server state: fetching, caching, background refetch |
| Motion | 12 | Animations — springs, shared-layout transitions, gestures |
| sonner | 2 | Toast notifications |
| lucide-react | — | Icon set (ships with shadcn) |
| React Compiler | — | Automatic memoisation — no manual `useMemo`/`useCallback` |
| oxlint | — | Fast Rust-based linter (replaces ESLint) |

### Why these choices

**Tailwind v4** drops the `tailwind.config.js` file entirely. Configuration moves into CSS (`@import "tailwindcss"`) and the Vite plugin handles the rest. Less config surface, faster cold-start.

**shadcn/ui (Base Nova preset)** is not a component library in the traditional sense — `npx shadcn add button` copies the component source into `src/components/ui/`. You own it and can change it freely. The Nova preset uses Base UI primitives instead of Radix UI, which are better maintained and lighter.

**Metadata-driven controls** lean on Base UI primitives that suit the data: a foreign-key field is a **Combobox** (`ui/combobox.tsx`) — type to filter by the referenced table's display labels, which scales past the handful-of-rows limit of a plain dropdown — and integer/number fields are a **NumberField** (`ui/number-field.tsx`) with hold-to-repeat steppers, scrub-to-change, and locale-aware formatting. Decimals stay a text input so precision is never lost to a float round-trip.

**TanStack Query** manages all server state. It handles loading, error, and stale states, caches responses, and refetches in the background so the UI stays fresh. This replaces the `useEffect` + `useState` pattern for data fetching entirely.

**React Compiler** (via `babel-plugin-react-compiler`) automatically optimises re-renders at build time. Write plain React — no manual `useMemo`, `useCallback`, or `React.memo` needed. The compiler inserts them for you where they help.

**oxlint** is orders of magnitude faster than ESLint. It catches the same categories of errors for React and TypeScript codebases.


## Project structure

```
src/
├── components/      Feature components, plus ui/ — shadcn primitives, owned in-repo
├── hooks/           Data hooks (TanStack Query) + view-state hooks (URL, prefs, selection)
├── lib/             Typed API client, error contract, display formatters, CSV import/export
├── types.ts         TypeScript mirror of the API's metadata + error contract
├── index.css        Tailwind v4 theme tokens
├── App.tsx          Renders the app shell
└── main.tsx         Entry point — wires the providers (theme, query, motion, toasts)
```

Because the UI is metadata-driven, components divide by *role*, not by table. The main
pieces are a **table view** (toolbar, filter bar, data grid, pagination), **record
panels** (a read-only detail and a create/edit form), and **field controls** that pick
the right input per column type — a combobox for foreign keys, a stepper for integers,
plain text for decimals. Bulk edit and delete, CSV import and export, a command palette,
and undoable deletes layer on top. Every one of them reads the live schema from the
backend's metadata endpoints, so adding a table or column updates the UI with no code change.


## Dev workflow

The frontend talks to the FastAPI backend through Vite's dev proxy, so three processes
must be running: the API (port 8000), the dev auth proxy (8001), and the Vite dev server
(5173). The backend and proxy setup lives in the root
[README → Running locally](../README.md#running-locally); the frontend piece is:

```powershell
cd frontend
npm install
npm run dev          # Vite dev server on http://localhost:5173
```

Access the app at **`http://localhost:5173`** — the Vite dev server is your browser target
during frontend development (not 8001).

The proxy config in `vite.config.ts` forwards `/api/*`, `/meta/*`, `/admin/*`, `/me`, and
`/.auth/*` to `http://localhost:8001`, where the auth proxy injects the EasyAuth headers
before passing the request on to FastAPI (`/me` powers the signed-in user badge; `/.auth`
is the session-refresh path):

```
Browser (5173) → Vite proxy → Auth proxy (8001) → FastAPI (8000) → Azure SQL
```

With all three running, visit `http://localhost:5173/meta/dbo` to confirm the chain — you
should get a live JSON response from the database.


## Adding shadcn components

Components are added on demand via the CLI. They are copied into `src/components/ui/` and are yours to modify:

```powershell
npx shadcn@latest add button
npx shadcn@latest add table
npx shadcn@latest add input
```

Browse available components at `https://ui.shadcn.com/docs/components`.


## Linting

```powershell
npm run lint
```

The linter is oxlint, configured in `.oxlintrc.json`. To enable type-aware rules (catches more but slower), install `oxlint-tsgolint` and add `"options": { "typeAware": true }` to the config.


## Testing

Two layers.

**Unit tests** run with Vitest (jsdom + Testing Library) and cover pure logic and component behaviour — formatters, CSV import/export, the API error mapping, query hooks, and the UI state panels:

```powershell
npm run test:unit
```

**End-to-end tests** run with Playwright against the production build, with every API response stubbed via request interception — so no backend or database is required.

```powershell
npx playwright install chromium   # first time only — downloads the browser
npm run build                     # the test server serves the built app
npm run test:e2e
```

The e2e specs live in `e2e/` and the in-memory API stub in `e2e/mock-api.ts`; `playwright.config.ts` starts a `vite preview` server automatically.


## Building for production

```powershell
npm run build
```

Output goes to `../backend/app/frontend/dist` (configured in `vite.config.ts`). FastAPI serves this directory as a static catch-all after all API routes. Running `npm run build` is useful for local preview (the backend then serves the built SPA at `http://localhost:8001`).

For **production**, you don't need to run this manually: the repo-root `Dockerfile` builds the frontend inside the image (multi-stage) and bakes the result into `app/frontend/dist`, so `docker build .` produces a complete, self-contained image. See `infra/README.md`.

The build runs `tsc -b` (type-check) before bundling, so TypeScript errors block the build.


## Environment and configuration

No `.env` file is needed for the frontend. The Vite proxy config in `vite.config.ts` is the only environment-specific setting, and it only applies to the dev server — in production there is no proxy because the frontend and backend share the same origin.

The auth proxy reuses your `az login` session (run `az login` first) — no tenant id to configure. See `backend/dev_auth_proxy.py` for details.
