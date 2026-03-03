import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Link from "next/link";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Auto Transcription",
  description: "End-to-end document digitalization platform",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={`${inter.className} bg-gray-50 text-gray-900 antialiased`}>
        <div className="flex min-h-screen flex-col">
          <header className="border-b border-gray-200 bg-white">
            <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
              <Link href="/" className="text-lg font-bold text-gray-900">
                Auto Transcription
              </Link>
              <nav className="flex items-center gap-6">
                <Link
                  href="/"
                  className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
                >
                  Upload
                </Link>
                <Link
                  href="/documents"
                  className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
                >
                  Documents
                </Link>
                <Link
                  href="/review"
                  className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
                >
                  Review
                </Link>
                <Link
                  href="/compliance"
                  className="text-sm font-medium text-gray-600 transition-colors hover:text-gray-900"
                >
                  Compliance
                </Link>
              </nav>
            </div>
          </header>
          <main className="flex-1">{children}</main>
        </div>
      </body>
    </html>
  );
}
