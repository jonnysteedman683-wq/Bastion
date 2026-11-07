# Supabase Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict the command centre to existing Supabase Auth users with a server-managed passwordless session.

**Architecture:** The existing Python server becomes the sole Supabase Auth client. It sends magic-link requests and verifies callback tokens through Auth's REST API, stores the returned access token in a secure `HttpOnly` cookie, and verifies that cookie with Auth before serving protected application or API routes. The React bundle gets a small login/session UI but still never contacts Supabase directly.

**Tech Stack:** Python 3.13 standard library, Supabase Auth REST API, React 19, TypeScript, Vitest, unittest.

**Spec:** `docs/superpowers/specs/2026-11-07-supabase-auth-design.md`

## Global Constraints

- Implement backlog item S1 only; do not touch guarded paths, deploy, or database schema.
- `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY` are configuration names only; never commit their values or any service-role key.
- Require server-side verification with Supabase Auth; never authorize from user metadata or browser storage.
- Preserve the existing browser-to-server-only ledger data boundary and read-only `bastion_reader` role.
- Authenticated responses and errors must be `no-store`; the login callback cookie is `HttpOnly`, `Secure`, `SameSite=Lax`, and path-scoped to `/`.
- Existing Supabase users may sign in; `should_create_user` is always false.

## Review Focus

- Missing or invalid Supabase configuration fails closed rather than exposing demo or live data.
- A forged, expired, or revoked cookie fails at the server verification boundary and cannot access `/api/*`.
- A magic-link request cannot create a new user.
- Callback inputs missing a token hash or receiving an Auth error do not issue a cookie.
- A cookie-setting response is never cacheable by an intermediary.

---

### Task 1: Server Auth Client and Guard

**Files:**
- Modify: `services/command-centre/app.py`
- Test: `services/command-centre/test_app.py`

**Interfaces:**
- Produces: `AuthClient.from_env(env) -> AuthClient`, `AuthClient.send_magic_link(email, callback_url) -> None`, `AuthClient.verify_magic_link(token_hash, token_type) -> str`, and `AuthClient.current_user(access_token) -> dict[str, object]`.
- Produces: guarded handler routes `POST /auth/magic-link`, `GET /auth/callback`, `POST /auth/sign-out`, and `GET /api/auth/me`.

- [ ] **Step 1: Write failing server tests**

Add hand-built HTTP/Auth fakes that prove: a missing auth configuration returns 503 for `/api/feed`; an unauthenticated request to `/api/feed` returns 401; a valid Auth user allows the existing feed response; a magic-link request sends `should_create_user: false`; a successful callback sets a `HttpOnly; Secure; SameSite=Lax` cookie plus `Cache-Control: no-store`; an invalid callback sets no cookie; and sign-out expires the cookie.

- [ ] **Step 2: Run the focused server tests to verify they fail**

Run: `python -m unittest services.command-centre.test_app`

Expected: FAIL because the auth client and protected routes do not yet exist.

- [ ] **Step 3: Implement the minimal server Auth boundary in `services/command-centre/app.py`**

Use only `urllib.request` and JSON. Call Supabase Auth with the publishable key, return a generic sign-in response that does not reveal user existence, and resolve the current user through the Auth user endpoint on every protected request. Gate application routes and `/api/*` before ledger access; permit `/login`, `/auth/*`, and static assets. Do not log cookies, tokens, or Auth response bodies.

- [ ] **Step 4: Run the focused server tests to verify they pass**

Run: `python -m unittest services.command-centre.test_app`

Expected: PASS.

### Task 2: Login and Session UI

**Files:**
- Modify: `services/command-centre/web/src/App.tsx`
- Create: `services/command-centre/web/src/lib/auth.ts`
- Create: `services/command-centre/web/src/lib/auth.test.ts`

**Interfaces:**
- Consumes: `GET /api/auth/me`, `POST /auth/magic-link`, and `POST /auth/sign-out` from Task 1.
- Produces: `getCurrentUser()`, `requestMagicLink(email)`, and `signOut()` browser helpers that call only the command-centre origin.

- [ ] **Step 1: Write failing client tests**

Test the auth helpers against a controlled `fetch` implementation: session requests include credentials, magic-link submission JSON-encodes the email and handles a non-success response, and sign-out sends a POST then reports completion. Name each test for the observable browser request/result rather than implementation details.

- [ ] **Step 2: Run the focused client tests to verify they fail**

Run: `npm test -- --run src/lib/auth.test.ts` from `services/command-centre/web`

Expected: FAIL because the auth module does not yet exist.

- [ ] **Step 3: Implement the minimal login/session UI**

Create a login view at `/login` with email input, clear generic confirmation/error states, and no Supabase client dependency. On an authenticated app route, display the user email and a sign-out control. Redirect unauthenticated application users to `/login`; after requesting a link, leave the user on the confirmation state.

- [ ] **Step 4: Run the focused client tests to verify they pass**

Run: `npm test -- --run src/lib/auth.test.ts` from `services/command-centre/web`

Expected: PASS.

### Task 3: Configuration, Documentation, and Regression Checks

**Files:**
- Modify: `services/command-centre/deploy.json`
- Modify: `services/command-centre/README.md`
- Modify: `docs/BACKLOG.md`

**Interfaces:**
- Consumes: environment configuration required by Task 1.
- Produces: documented Secret Manager configuration names and an S1 completion marker.

- [ ] **Step 1: Add the minimal non-secret deployment configuration and operator guidance**

Declare the two configuration secret names in `deploy.json`. Document the required Supabase Auth URL/callback and Magic Link template setup without adding secrets, and tick S1 only after the implementation checks pass.

- [ ] **Step 2: Run all required checks**

Run:

```powershell
python -m tools.quartermaster validate
python -m unittest discover -s tests -t .
Get-ChildItem services -Directory | ForEach-Object { python -m unittest discover -s $_.FullName }
Set-Location services/command-centre/web; npm test; npm run build
```

Expected: every command passes with zero test failures.

- [ ] **Step 3: Commit the backlog item**

```powershell
git add docs/BACKLOG.md docs/superpowers/specs/2026-11-07-supabase-auth-design.md docs/superpowers/plans/2026-11-07-supabase-auth.md services/command-centre
git commit -m "feat: protect command centre with Supabase Auth"
```
