import type { NextConfig } from "next";

/**
 * The dashboard talks to the backend over the same origin.
 *
 * Proxying `/api` and `/media` instead of pointing the browser at :8000 keeps
 * the session cookie first-party and means a signed media URL works exactly as
 * issued. It also removes CORS from the demo's failure surface entirely, which
 * matters because the one thing that must never break at 3 AM is a clip.
 */
const backend = process.env.AEGIS_API ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/media/:path*", destination: `${backend}/media/:path*` },
      { source: "/health", destination: `${backend}/health` },
    ];
  },
};

export default nextConfig;
