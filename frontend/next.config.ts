import type { NextConfig } from "next";

// Deployed on Vercel, the frontend runs alone as the static live demo (answers come
// from public/demo/*.json). Set NEXT_PUBLIC_DEMO_MODE=0 there to point it at a real API.
const demoMode = process.env.NEXT_PUBLIC_DEMO_MODE ?? (process.env.VERCEL ? "1" : "");

// Optional same-origin proxy to the API (used in Codespaces, where the API's forwarded
// port lives on another origin): build with NEXT_PUBLIC_API_URL="" and
// API_PROXY_TARGET=http://api:8000 so the browser only ever talks to the frontend.
const apiProxyTarget = process.env.API_PROXY_TARGET;

const nextConfig: NextConfig = {
  // Self-contained server bundle for the Docker image.
  output: "standalone",
  env: { NEXT_PUBLIC_DEMO_MODE: demoMode },
  async rewrites() {
    return apiProxyTarget ? [{ source: "/api/v1/:path*", destination: `${apiProxyTarget}/api/v1/:path*` }] : [];
  },
  cacheComponents: true,
  partialPrefetching: true,
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
