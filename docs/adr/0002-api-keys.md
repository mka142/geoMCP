# ADR 0002: API keys through FastMCP's TokenVerifier

Status: accepted (M0).

## Decision

- HTTP mode requires a bearer token; FastMCP's `TokenVerifier` base class handles header parsing, `401` responses and protected-resource metadata.
  We implement only `verify_token`, which looks the key up in SQLite.
- Keys look like `gmk_<id>_<secret>`; the id is the lookup key and only `sha256(secret)` is stored, compared in constant time.
  The secret is 256 random bits, so a fast hash is appropriate; slow password hashes (Argon2, bcrypt) protect low-entropy human passwords.
- `geomcp-admin keys create|list|revoke` manages keys; a token is shown once.
- stdio mode has no auth: the user already controls the process.

## Not now

OAuth (FastMCP ships providers for Google, GitHub, WorkOS and others) can replace or complement keys without changing the tools.
