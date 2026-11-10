# Bastion security notes

## Command-centre security baseline

This project is designed around a private command-centre pattern: the browser talks only to the Bastion service, and agent identity is resolved server-side from a secret registry rather than from client input.

## Security hardening already in place

- API routes for private data are authenticated.
- Ingest requests require a Bearer token mapping to an approved agent key.
- The browser is isolated from Supabase and never receives database credentials.
- Static files are served from a controlled root with path traversal checks.
- Security headers include `X-Content-Type-Options`, `Referrer-Policy`, and a restrictive CSP.

## Additional hardening applied

- Magic-link callback URLs can use a trusted `BASTION_PUBLIC_URL`/`PUBLIC_BASE_URL` origin when configured, instead of trusting the raw incoming host header.
- Request bodies are capped at a safe size for auth and ingest routes.
- Ingest summary values are length-limited before they are stored.
- Oversized or malformed payloads return explicit HTTP 413/400 failures rather than processing unbounded input.

## Important operational rule

Do not accept an `agent` value from the browser. The server must derive agent identity from the agent-specific secret registry and validate all event content before writing it to the ledger or local ingest buffer.

## Command-centre guidance

For the visual orchestration view, the safe pattern is:

- read-only mission map
- server-side graph generation from ledger data
- explicit agent assistance requests with pending/accept/reject states
- owner-only approvals and kill-switch actions
- all state changes logged for audit and review

This keeps the dashboard useful without weakening the security model.
