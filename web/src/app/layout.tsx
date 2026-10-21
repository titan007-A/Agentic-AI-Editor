import type { Metadata, Viewport } from "next";
import { Bricolage_Grotesque, Caveat } from "next/font/google";

import { Shell } from "@/components/shell";
import { Toaster } from "@/components/ui/sonner";

import "./globals.css";

const body = Bricolage_Grotesque({ subsets: ["latin"], variable: "--font-body" });
const hand = Caveat({ subsets: ["latin"], weight: ["500", "700"], variable: "--font-hand-face" });

export const metadata: Metadata = {
  title: "Agentic AI Editor",
  description: "Local video editor for short-form clips",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#f2eadb",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${body.variable} ${hand.variable} h-full antialiased`}>
      <body className="min-h-full bg-background text-foreground">
        <Shell>{children}</Shell>
        <Toaster position="top-center" />
      </body>
    </html>
  );
}
