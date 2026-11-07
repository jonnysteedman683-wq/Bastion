# Supabase Auth for Command Centre

## Intent

Replace the shared dashboard token with individual, passwordless Supabase Auth identities. Only existing Supabase Auth users may access the command centre; the browser continues to communicate solely with the command-centre server for application data.

## Scope

- Add email magic-link sign-in, callback handling, sign-out, and a current-user endpoint.
- Store the Supabase access token only in a secure, `HttpOnly`, `SameSite=Lax` cookie.
- Require a current, server-verified Supabase user for every command-centre application route and API route.
- Allow only the sign-in/callback/logout endpoints and static build assets to remain unauthenticated.
- Configure the service with `SUPABASE_URL` and `SUPABASE_PUBLISHABLE_KEY`; do not add a service-role key, database role, table, or RLS policy.

## Non-goals

- No direct browser-to-Supabase access, no automatic user creation, social sign-in, role model, schema migration, or change to the ledger's read-only database role.
- Cloudflare Access remains the separately planned S2 defence-in-depth layer.

## Approach

Use the Supabase Auth REST endpoints from the existing standard-library Python server. Sign-in requests use `should_create_user: false`; the callback verifies the magic-link token hash, sets the session cookie, and redirects home. Each protected request resolves the cookie token through Supabase Auth's user endpoint, so revoked sessions are rejected. Missing configuration fails closed with a service-unavailable response.

## Deployment prerequisite

In Supabase Auth URL configuration, register the deployed `/auth/callback` URL. Configure the Magic Link email template to send `token_hash` and `type=email` to that callback, as required for server-side verification.
