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
    <html
      lang="en"
      className={`${body.variable} ${hand.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        {/* Before the first paint, so a night-look page doesn't flash cream; the toggle
            takes it off again for anyone who isn't an admin. */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var t=localStorage.getItem('aiedit-theme');if(t==='dark')t='midnight';if(t==='midnight'||t==='darkroom'||t==='blueprint'){document.documentElement.classList.add('dark');document.documentElement.setAttribute('data-night',t)}}catch(e){}",
          }}
        />
      </head>
      <body className="min-h-full bg-background text-foreground">
        <Shell>{children}</Shell>
        <Toaster position="top-center" />
      </body>
    </html>
  );
}
