import type { Metadata, Viewport } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import { SessionGate } from "@/components/session-gate";
import { WorkspaceProvider } from "@/components/workspace-provider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Aegis — private care console",
  description:
    "A privacy-first care console that watches the rooms a family chooses and only interrupts when something actually needs them.",
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
  themeColor: "#f7f8f7",
  viewportFit: "cover",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      data-scroll-behavior="smooth"
      className={`${GeistSans.variable} ${GeistMono.variable}`}
    >
      <body>
        <SessionGate><WorkspaceProvider>{children}</WorkspaceProvider></SessionGate>
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
