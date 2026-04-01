import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Toaster } from "sonner";
import { ThemeProvider } from "@/providers/theme-provider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Sidebar } from "@/components/common/nav";
import { Header } from "@/components/common/header";
import { CommandPalette } from "@/components/common/command-palette";
import { MobileBottomNav, MobileNavSheet } from "@/components/common/mobile-nav";
import { GlobalProcessingBar } from "@/components/common/global-processing-bar";
import { MainContent } from "@/components/common/main-content";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AutoTranscript | Document Processing Platform",
  description: "Enterprise AI-powered document processing, extraction, and compliance review",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <ThemeProvider>
          <TooltipProvider delayDuration={0}>
            <Sidebar />
            <MobileNavSheet />

            <MainContent>
              <Header />
              <GlobalProcessingBar />
              <main>
                {children}
              </main>
            </MainContent>

            <MobileBottomNav />
            <CommandPalette />

            <Toaster
              position="bottom-right"
              toastOptions={{
                className: "bg-card text-card-foreground border-border shadow-lg",
                style: { borderRadius: "12px", fontSize: "13px" },
              }}
            />
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
