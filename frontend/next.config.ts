import type { NextConfig } from "next";

// Backend base URL used by the /api/* proxy rewrite below (server-side only).
// Set BACKEND_URL in Vercel to the Render service URL (no trailing slash).
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  /* config options here */
  cacheComponents: true,
  partialPrefetching: true,
  // Proxy /api/* to the FastAPI backend so the browser makes same-origin
  // requests (no CORS preflight).
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${BACKEND_URL.replace(/\/+$/, "")}/api/:path*`,
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
