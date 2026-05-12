import type { NextConfig } from "next";

/**
 * Dev-time proxy: any ``/api/*`` request from the browser is
 * forwarded to the FastAPI backend. Keeps the OCR markdown's
 * ``<img src="/api/documents/.../HASH_img.jpg">`` references
 * working when rendered from the Next dev server (browser sees
 * a same-origin relative URL, Next proxies the request to the
 * backend port). Without this, the browser tries to load
 * ``http://localhost:3100/api/...`` which the Next server doesn't
 * serve — every signature crop renders broken.
 *
 * Same-origin proxying also avoids CORS preflights on the
 * ``fetch()`` calls in ``src/lib/api.ts`` when those switch to
 * relative paths (none do today, but adding the proxy makes
 * that refactor a no-op when we get to it).
 *
 * The backend URL is read from ``NEXT_PUBLIC_API_URL`` (the same
 * env var ``src/lib/api.ts`` uses), defaulting to
 * ``http://localhost:8100``. Production deployments typically
 * handle this path via nginx / ingress / Vercel rewrites — set
 * the env var to point at the public backend URL if needed.
 */
const BACKEND_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8100";

const nextConfig: NextConfig = {
  experimental: {
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
