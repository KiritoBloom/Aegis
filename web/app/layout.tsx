import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { Newsreader } from "next/font/google";
import "./globals.css";

/**
 * Three faces, three jobs.
 *
 * Newsreader carries the one number the product is actually arguing about, and
 * nothing else — a serif numeral reads as editorial confidence rather than
 * telemetry. Geist Sans is the UI. Geist Mono is everything a machine produced,
 * which is a useful honesty signal: if a value is monospaced, it is a fact, not
 * a paraphrase.
 */
const newsreader = Newsreader({
  subsets: ["latin"],
  weight: ["400", "500"],
  style: ["normal", "italic"],
  variable: "--font-newsreader",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Aegis — Margaret's home",
  description:
    "A privacy-first care system that watches a home through one ordinary camera and only interrupts when it matters.",
  applicationName: "Aegis",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "Aegis", statusBarStyle: "default" },
  icons: {
    icon: [
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { url: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
    apple: [{ url: "/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  themeColor: "#f6f3ed",
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} ${newsreader.variable}`}
    >
      <body>
        {children}
        {/*
          Registered from the root layout rather than the profile page so the
          worker is alive before anyone opens settings — a push that arrives
          while the app is closed cannot be handled by a worker that was never
          installed. It is a no-op where service workers are unavailable.
        */}
        <script
          dangerouslySetInnerHTML={{
            __html: `if ("serviceWorker" in navigator) {window.addEventListener("load", function () {navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(function () {});});}`,
          }}
        />
      </body>
    </html>
  );
}
