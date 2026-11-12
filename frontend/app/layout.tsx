import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { ExperienceTracker } from "@/components/experience-tracker";
import "./globals.css";
import "./book.css";
import "./book-introduction.css";

export const metadata: Metadata = { title: "忆谈", description: "给老人的回忆录访谈" };
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#fbf7ef" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased"><Suspense fallback={null}><ExperienceTracker /></Suspense>{children}</body>
    </html>
  );
}
