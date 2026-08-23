import type { Metadata } from "next";
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import "./globals.css";
import { AppShell } from "@/components/app-shell";

export const metadata: Metadata = {
  title: "SENTINEL-X — Border Security Hub",
  description: "AI-driven video analytics for border security operations (IBVAP).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="dot-grid-bg min-h-screen">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
