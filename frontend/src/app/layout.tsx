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
import { TraceFetchInit } from "@/components/common/trace-fetch-init";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AutoAudit AI | Agentic Quality Assurance Platform",
  description: "Agentic platform for extraction quality assurance, completeness validation, and compliance review",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <ThemeProvider>
          <TraceFetchInit />
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
