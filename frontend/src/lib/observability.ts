/**
 * Trace-aware fetch wrapper.
 *
 * Adds `traceparent` on outbound requests (if we have a current trace
 * context) and reads back `X-Request-Id` from responses so the DevTools
 * network panel and our error toasts can display the id for copy-paste
 * into the backend log.
 *
 * Implementation choice: random per-request trace when none is bound. We
 * do NOT maintain a session-wide trace because that would wrongly tie
 * unrelated UI actions under one id.
 */

const HEX = "0123456789abcdef";

function randomHex(len: number): string {
  let out = "";
  for (let i = 0; i < len; i++) out += HEX[Math.floor(Math.random() * 16)];
  // Ensure non-zero to stay within the W3C Trace Context spec.
  if (/^0+$/.test(out)) out = HEX[1] + out.slice(1);
  return out;
}

export function mintTraceparent(): string {
  const traceId = randomHex(32);
  const spanId = randomHex(16);
  return `00-${traceId}-${spanId}-01`;
}

const LAST_REQUEST_ID_KEY = "__bmr_last_request_id";

export function lastRequestId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(LAST_REQUEST_ID_KEY);
}

function recordRequestId(id: string | null) {
  if (typeof window === "undefined" || !id) return;
  try {
    window.localStorage.setItem(LAST_REQUEST_ID_KEY, id);
  } catch {
    /* ignore storage quota / private mode */
  }
}

/**
 * Trace-injected fetch. Drop-in replacement for global `fetch` — same
 * signature, same return type. Always sends `traceparent`; captures the
 * response's `X-Request-Id` so the UI can surface it on errors.
 */
export async function tracedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers || {});
  if (!headers.has("traceparent")) {
    headers.set("traceparent", mintTraceparent());
  }
  const res = await fetch(input, { ...init, headers });
  const rid = res.headers.get("X-Request-Id");
  recordRequestId(rid);
  return res;
}
