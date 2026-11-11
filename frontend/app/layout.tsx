import type { Metadata, Viewport } from "next";
import "./globals.css";
import "./book.css";
import "./book-introduction.css";

export const metadata: Metadata = { title: "忆谈", description: "给老人的回忆录访谈" };
export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#fbf7ef" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body className="antialiased">{children}</body>
    </html>
  );
}
