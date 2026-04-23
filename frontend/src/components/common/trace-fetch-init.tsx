"use client";

/**
 * Client-side fetch trace injector.
 *
 * Patches `window.fetch` exactly once at app startup so every request
 * carries a `traceparent` header and echoes back the server's
 * `X-Request-Id` into `localStorage.__bmr_last_request_id`. Call sites
 * stay the same — they keep using the global `fetch`.
 *
 * The patch is a no-op if `traceparent` is already on the outbound
 * request (respects callers that want to set their own).
 */

import { useEffect } from "react";

import { mintTraceparent } from "@/lib/observability";

const PATCHED = Symbol.for("bmr.trace-fetch.patched");

type PatchedWindow = typeof globalThis & { [PATCHED]?: boolean };

export function TraceFetchInit() {
  useEffect(() => {
    if (typeof window === "undefined") return;
    const w = window as PatchedWindow;
    if (w[PATCHED]) return;
    w[PATCHED] = true;

    const originalFetch = window.fetch.bind(window);

    window.fetch = async (input, init) => {
      const headers = new Headers(init?.headers || {});
      if (!headers.has("traceparent")) {
        headers.set("traceparent", mintTraceparent());
      }
      const res = await originalFetch(input, { ...init, headers });
      const rid = res.headers.get("X-Request-Id");
      if (rid) {
        try {
          window.localStorage.setItem("__bmr_last_request_id", rid);
        } catch {
          /* private mode / quota — ignore */
        }
      }
      return res;
    };
  }, []);
  return null;
}
