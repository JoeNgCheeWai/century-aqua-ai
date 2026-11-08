import type { NextConfig } from "next";

// Backend base URL used by the /api/* proxy rewrite below (server-side only).
// Set BACKEND_URL in Vercel to the Render service URL (no trailing slash).
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  // Same-origin proxy to the FastAPI backend (no CORS preflight). ONLY the
  // health check goes straight through. /api/chat is handled by the rate-limited
  // route handler in src/app/api/chat/route.ts, and the internal-only /api/cao
  // is deliberately NOT proxied, so it is not reachable through this site.
  async rewrites() {
    return [
      {
        source: "/api/health",
        destination: `${BACKEND_URL.replace(/\/+$/, "")}/api/health`,
      },
    ];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
