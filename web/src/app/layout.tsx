import type { Metadata, Viewport } from "next";

import { Shell } from "@/components/shell";
import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

export const metadata: Metadata = {
  title: "Agentic AI Editor",
  description: "Local video editor for short-form clips",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#0a0a0a",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark h-full antialiased">
      <body className="min-h-full bg-background text-foreground">
        <Shell>{children}</Shell>
        <Toaster position="top-center" />
      </body>
    </html>
  );
}
