# Contract — Trace Header

**Satisfies**: FR-001 (`traceparent` + `X-Request-Id` on every response), FR-002 (trace_id on every log line), SC-001 (single-id correlation), SC-007 (OTLP-compatible shape).

Defines the exact parse, emit, and failure-mode rules for the correlation headers Spec 006 introduces. Downstream services (frontend fetch client, any future service mesh, future OTEL collector) follow this document.

---

## Headers

### `traceparent` (primary)

Format — W3C Trace Context v3 (all lower-case hex):

```
traceparent: <version>-<trace_id>-<parent_id>-<flags>
```

- `<version>`: `00` for this spec version.
- `<trace_id>`: 32 hex chars, 128-bit, lower-case. Not `00000000000000000000000000000000`.
- `<parent_id>`: 16 hex chars, 64-bit, lower-case. Not `0000000000000000`. This is the caller's span id, i.e. the parent of the span the handler will create.
- `<flags>`: 2 hex chars. `01` = sampled, `00` = not-sampled. Default emitted: `01`.

Example:

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

### `tracestate` (passthrough)

Opaque vendor-specific segments. The server does not parse or modify this header; it echoes it unchanged on the response if present on the request. Absent → omit from response.

### `X-Request-Id` (convenience)

Format:

```
X-Request-Id: <trace_id>
```

A 32-char lower-case hex string. Always equals the `trace_id` from `traceparent`. Safe for copy-paste into `grep`. Clients may send this header; the server reads it ONLY if `traceparent` is absent, and even then only treats it as a hint — a missing/invalid `trace_id` triggers server-side minting.

---

## Parsing rules (server-side)

Pseudocode:

```
ctx = parse(request.headers.get("traceparent"))
if ctx is None:
    hint = request.headers.get("X-Request-Id")
    ctx = try_from_request_id(hint) or mint_new()
bind(TRACE_CTX, ctx)
```

`parse(value) -> TraceContext | None` succeeds iff:

- `value` matches the regex `^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$` (version 00 only in v0).
- `trace_id` is not all-zero.
- `parent_id` is not all-zero.

Any other shape → `None`. Malformed headers emit one `warn` log `trace.malformed_header` with the offending raw value (truncated to 128 chars, redaction-safe) and fall through to `X-Request-Id` hint, then to minting.

`try_from_request_id(value)`:

- If `value` matches `^[0-9a-f]{32}$`, use it as `trace_id` and mint a fresh `parent_id`, `flags="01"`.
- Otherwise, return `None`.

`mint_new()`:

- `trace_id`: 16 random bytes (128 bits) via `secrets.token_hex(16)`, ensuring the result is not all-zero (probability 2^-128).
- `parent_id`: 8 random bytes via `secrets.token_hex(8)`, non-zero.
- `flags`: `"01"`.

---

## Emission rules (server-side)

Every response carries:

```
traceparent: 00-<trace_id>-<handler_span_id>-<flags>
X-Request-Id: <trace_id>
tracestate: <passthrough if present>
Access-Control-Expose-Headers: traceparent, X-Request-Id, tracestate
```

`<handler_span_id>` is the span id of the span the handler itself ran under (i.e. the child of the incoming `parent_id`), so the caller can chain child spans relative to the handler, not relative to its own already-completed span.

`Access-Control-Expose-Headers` is appended (not replacing) any existing value so browser fetches can read these headers — critical for the frontend observability client to echo the id back.

---

## Child spans (in-process)

Creating a child span (e.g. inside `@traced("compliance.agent.alcoa")`):

- `trace_id`: inherited from parent.
- `parent_span_id`: the currently-active `span_id` (parent).
- `span_id`: freshly minted via `secrets.token_hex(8)`, non-zero.
- `flags`: inherited.

The decorator wraps the function in a `with span(...):` context manager that pushes the child context onto the `TRACE_CTX` ContextVar and pops it on exit.

---

## Failure modes

| Scenario | Server behaviour |
|---|---|
| No `traceparent`, no `X-Request-Id` | Mint a new context. Emit `trace.request.started` at `info` with `source=minted`. |
| Valid `traceparent` | Parse, adopt. Emit `trace.request.started` with `source=inbound`. |
| Malformed `traceparent`, no `X-Request-Id` | Mint. Emit `trace.malformed_header` at `warn` with raw header (truncated). Do NOT reject the request. |
| Valid `traceparent`, different `X-Request-Id` | Trust `traceparent`. Emit `trace.header_mismatch` at `warn` with both values. |
| Trace context is lost inside an executor | Worker thread emits `trace.context.lost` at `warn` and mints a local context so log lines are not uncorrelated from each other even though they're detached from the caller. |

---

## Interoperability

- Any OpenTelemetry-conformant client or server can talk to this service via `traceparent`. This is the entire point — it's why we chose the W3C shape.
- `X-Request-Id` is an additional channel for humans and tools that don't speak tracing. The two carry the same `trace_id`.
- Future OTLP integration: when we add `opentelemetry-sdk` + `opentelemetry-exporter-otlp`, the existing `TraceContext` objects map 1:1 to `opentelemetry.trace.SpanContext`. No call-site changes needed.
