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

/**
 * Conservative headers only.
 *
 * The CSP deliberately carries no script/style/connect directives: Next injects
 * inline bootstrap scripts, and a restrictive default-src would break them.
 * `frame-ancestors` gives the same clickjacking protection as X-Frame-Options
 * without touching how the app loads. Camera and microphone stay enabled for
 * this origin because the setup and live pages use them.
 */
const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
  },
  {
    key: "Permissions-Policy",
    value: "camera=(self), microphone=(self), geolocation=(), payment=(), usb=()",
  },
];

const nextConfig: NextConfig = {
  reactStrictMode: true,
  devIndicators: false,
  poweredByHeader: false,
  /**
   * Compression has to stay off.
   *
   * Next wraps every response in the `compression` middleware, and that
   * middleware buffers until it has a flush point. `/api/events/stream` is an
   * infinite `text/event-stream`, so it never gets one: the browser opens the
   * connection, reads nothing, and every live update - verification stages,
   * alert verdicts, check-ins - is silently lost. The console then freezes on
   * whatever the last `/api/wall/frame` response said.
   *
   * The service is local-first and its payloads are small JSON frames, so
   * gzip buys nothing worth trading the live channel for.
   */
  compress: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/media/:path*", destination: `${backend}/media/:path*` },
      { source: "/health", destination: `${backend}/health` },
    ];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
