# Frontend — Claim Automation Dashboard

This folder contains the web user interface for the Claim Automation project. It is a
**React** application written in **TypeScript**, built with **Vite**, and tested with
**Vitest**. In production it is deployed to Azure Static Web Apps; in development it runs
locally and talks to the Python backend.

This document is written for readers who are **not familiar with TypeScript**. It explains
what every file does, what each tool is for, and why the choices made here follow
TypeScript and React best practices.

---

## 1. What is TypeScript, in one paragraph?

Browsers only understand JavaScript. JavaScript, however, has no way to declare what kind
of data a variable holds — a function expecting a number will happily accept a string and
fail only when a user clicks the wrong button in production. **TypeScript is JavaScript
plus type annotations**: you write what each value is supposed to be, and a compiler
(`tsc`) checks the whole program _before_ it ever runs. The types are then erased, and
plain JavaScript is shipped to the browser. In short: TypeScript moves a whole class of
runtime bugs to compile time, where they cost seconds instead of incidents.

Files ending in `.ts` are TypeScript; files ending in `.tsx` are TypeScript that also
contains JSX (React's HTML-like syntax for describing user interfaces).

---

## 2. Folder contents

```
frontend/
├── index.html            # The single HTML page the app mounts into
├── src/
│   ├── main.tsx          # Entry point: mounts the React app into the page
│   ├── App.tsx           # The application component (health-check dashboard)
│   ├── App.test.tsx      # Tests for App.tsx (co-located with the code)
│   └── setupTests.ts     # One-time test environment setup
├── package.json          # Dependencies and command scripts
├── tsconfig.json         # TypeScript compiler configuration
├── vite.config.ts        # Vite (build tool) + Vitest (test runner) configuration
├── eslint.config.js      # Linter rules (static code-quality checks)
├── .prettierrc.json      # Code formatter configuration
└── dist/                 # Build output (generated; not edited by hand)
```

### `index.html`

The only HTML page in the app. It contains an empty `<div id="root">` and loads
`src/main.tsx`. This is the **Single-Page Application (SPA)** pattern: the page is a
shell, and React renders everything inside it.

### `src/main.tsx` — the entry point

```tsx
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

Two things worth explaining:

- **`<StrictMode>`** is a React development aid that surfaces unsafe patterns early
  (e.g. it deliberately runs effects twice in development to catch missing cleanup).
  Wrapping the app in it is the officially recommended default.
- **The `!` after `getElementById("root")`** is a TypeScript _non-null assertion_.
  `getElementById` is typed as possibly returning `null`, and TypeScript refuses to use a
  maybe-null value without a check. Here the element is guaranteed to exist because we own
  `index.html`, so the code asserts that explicitly. Note that TypeScript forced this
  decision to be _visible_ — in plain JavaScript, a missing element would just crash.

### `src/App.tsx` — the application component

The current feature (the "walking skeleton") is deliberately tiny: on load, the app calls
the backend's `GET /api/health` endpoint and shows one of three states — _checking_,
_all good_, or _backend unavailable_. Small as it is, it demonstrates several best
practices:

- **A union type instead of loose strings.**

  ```ts
  type Status = "loading" | "ok" | "error";
  ```

  This declares that the component's state can be _exactly_ one of three values. If a
  future edit writes `setStatus("okk")`, the compiler rejects it immediately. In plain
  JavaScript that typo would silently render nothing. This is TypeScript's signature
  technique: **make invalid states unrepresentable**.

- **Typed React state.**

  ```ts
  const [status, setStatus] = useState<Status>("loading");
  ```

  The `<Status>` part ties the state variable to the union type above, so every read and
  write of `status` is checked.

- **Typed handling of untrusted data.** The backend response is typed as
  `{ status?: string }` — the `?` means "this field may be absent". The code then checks
  the value explicitly (`body.status === "ok"`) instead of trusting it. Data crossing a
  network boundary is never assumed to have the right shape.

- **Effect cleanup (the `cancelled` flag).** If the user navigates away while the request
  is still in flight, the `useEffect` cleanup function flips `cancelled` and the response
  is ignored. This prevents React's "state update on an unmounted component" bug — a
  standard React idiom for fetch-in-effect.

- **Errors are handled exhaustively.** Network failure, non-200 HTTP status, and a 200
  response with an unexpected body all land in the same explicit `error` state. Nothing is
  left to fall through silently.

- **No hardcoded backend URL.** The app calls the _relative_ path `/api/health`. In
  development, Vite proxies `/api/*` to the local backend (see `vite.config.ts`); in
  production, Azure Static Web Apps routes it. The same code runs unchanged in both
  environments — configuration lives in config files, not in application code.

### `src/App.test.tsx` — the tests

Tests live **next to the code they test** (co-location), the convention for this repo and
common in the React ecosystem. They use **React Testing Library**, whose philosophy is to
test what the _user_ sees (`screen.getByText(/all good/i)`) rather than internal
implementation details — so tests survive refactoring.

Five cases cover every branch of the component: success, network failure, non-OK HTTP
status, an OK status with an unexpected body, and the loading state. Each test name
references the requirement it verifies (e.g. `REQ-2.1` from
[`.specs/walking-skeleton/`](../.specs/)), keeping tests traceable to the spec. Per the
project constitution, these tests were written **before** the implementation (TDD).

The `fetch` network call is mocked with `vi.spyOn`, and `afterEach` restores all mocks so
no test can leak state into the next one.

### `src/setupTests.ts`

A one-line file loaded once before the test suite runs. It registers `jest-dom`, which
adds human-readable assertions such as `toBeInTheDocument()`. Keeping test-only setup in a
dedicated file (wired up in `vite.config.ts`) keeps test machinery out of production code.

---

## 3. The configuration files, and why they matter

### `tsconfig.json` — the TypeScript compiler settings

This is where "adheres to best practices" is most concretely enforced. The important
lines:

| Setting                                               | What it does                                                                                                                      | Why it's best practice                                                                                                                                                       |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `"strict": true`                                      | Turns on **all** of TypeScript's strict checks at once (no implicit `any` types, mandatory null-checks, strict function types, …) | The single most important TypeScript setting. Without it, TypeScript quietly degrades toward unchecked JavaScript. Official guidance is to enable it on every new project.   |
| `"noUnusedLocals"` / `"noUnusedParameters"`           | Compile error for unused variables/parameters                                                                                     | Dead code is flagged at compile time instead of accumulating.                                                                                                                |
| `"noFallthroughCasesInSwitch"`                        | Compile error for a `switch` case that falls into the next one without `break`                                                    | Catches a classic silent-bug pattern.                                                                                                                                        |
| `"noEmit": true`                                      | The compiler only _checks_ types; it produces no JavaScript                                                                       | Type checking and code generation are separated: Vite does the (much faster) compilation, `tsc` acts purely as the correctness gate. This is the standard modern Vite setup. |
| `"isolatedModules"` / `"moduleDetection": "force"`    | Guarantees every file can be compiled independently                                                                               | Required for fast bundler-based builds to be safe.                                                                                                                           |
| `"target": "ES2022"`, `"moduleResolution": "bundler"` | Modern JavaScript output and import resolution matching how Vite actually works                                                   | No legacy transpilation baggage.                                                                                                                                             |

The practical consequence: `npm run build` runs `tsc -b` **before** bundling, so a type
error anywhere in `src/` fails the build. Type safety is not advisory here — it is a
hard gate.

### `vite.config.ts` — build tool and test runner

Vite is the dev server and bundler (it serves the app in development with instant reload
and produces the optimized `dist/` output for production). Two notable choices:

- **The `/api` dev proxy** forwards API calls to the local backend at `localhost:8000`, so
  the frontend never needs to know a backend URL (see `App.tsx` above).
- **Vitest is configured in the same file.** Vitest is Vite's companion test runner; it
  reuses the exact same build pipeline for tests, so "works in tests" and "works in the
  browser" cannot drift apart. `environment: "jsdom"` gives tests a simulated browser DOM
  without needing a real browser.

Note that the config file itself is TypeScript (`vite.config.ts`) — even the tooling
configuration is type-checked.

### `eslint.config.js` — the linter

ESLint performs static analysis beyond what the type checker covers. This project uses:

- `@eslint/js` recommended rules — general JavaScript pitfalls,
- `typescript-eslint` recommended rules — the community-standard TypeScript rule set
  (e.g. it flags uses of the type-safety escape hatch `any`),
- `eslint-plugin-react-hooks` — enforces React's _Rules of Hooks_, catching subtle bugs
  such as a `useEffect` with a missing dependency,
- `eslint-plugin-react-refresh` — keeps components hot-reload-safe in development.

### `.prettierrc.json` — the formatter

Prettier formats all code automatically. The file is `{}` on purpose: it means **"use
Prettier's defaults, no debates"**. Formatting is checked in CI-style via
`npm run lint` (which runs `prettier --check`), so style is uniform and never reviewed by
humans.

### `package.json` — dependencies and scripts

Two things worth noting for a non-TypeScript reader:

- **Runtime dependencies are minimal**: just `react` and `react-dom`. Everything else
  (TypeScript, Vite, Vitest, ESLint, Prettier, testing libraries, and the `@types/*`
  packages that provide TypeScript type definitions for React) is a `devDependency` —
  used to build and verify the app, never shipped to the user.
- **`"type": "module"`** opts into modern standard JavaScript modules (`import`/`export`)
  rather than legacy formats.

---

## 4. Everyday commands

Run these from the `frontend/` folder (or use the repo-root `Makefile`, which wraps them):

| Command              | What it does                                                                      |
| -------------------- | --------------------------------------------------------------------------------- |
| `npm install`        | Install dependencies                                                              |
| `npm run dev`        | Start the dev server with hot reload (proxies `/api` to the backend on port 8000) |
| `npm test`           | Run the test suite once                                                           |
| `npm run test:watch` | Run tests continuously while editing                                              |
| `npm run lint`       | Type-aware lint + formatting check                                                |
| `npm run format`     | Auto-format all files                                                             |
| `npm run build`      | Type-check (`tsc -b`), then produce the production bundle in `dist/`              |

---

## 5. Best-practices summary

For a reviewer who wants the one-screen justification:

1. **Strict mode everywhere** — `"strict": true` plus extra checks; type errors fail the
   build (`tsc -b` runs before every production build).
2. **Types model the domain** — component state is a closed union
   (`"loading" | "ok" | "error"`), making invalid states unrepresentable.
3. **Untrusted data is not trusted** — API responses are typed as optional and validated
   at the boundary.
4. **Escape hatches are explicit and rare** — a single, justified non-null assertion in
   `main.tsx`; the `typescript-eslint` rules flag any use of `any`.
5. **Tests first, user-centric, exhaustive** — TDD per the project constitution; React
   Testing Library queries by visible text; all component branches covered; tests
   co-located and traceable to spec requirements.
6. **Layered automated quality gates** — compiler (types) → ESLint (correctness patterns,
   React hook rules) → Prettier (formatting), each doing one job.
7. **Modern, boring toolchain** — Vite + Vitest sharing one pipeline, ES modules,
   ES2022 target, minimal runtime dependencies.
8. **Environment-independent code** — relative `/api` paths with proxying/routing in
   config, so the same bundle runs in dev and on Azure unchanged.
