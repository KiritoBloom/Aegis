import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "Aegis — Check-in",
  description: "A question from Aegis for the resident.",
};

export const viewport: Viewport = {
  themeColor: "#f6f3ed",
};

export default function CheckinLayout({ children }: { children: React.ReactNode }) {
  return children;
}
