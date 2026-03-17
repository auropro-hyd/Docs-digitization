"use client";

import { AlertTriangle, RotateCcw, Home } from "lucide-react";
import { Button } from "@/components/ui/button";
import Link from "next/link";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div
      className="flex flex-col items-center justify-center min-h-[60vh] px-6 text-center"
      role="alert"
    >
      <div className="size-14 rounded-xl bg-destructive/10 flex items-center justify-center mb-6">
        <AlertTriangle className="size-7 text-destructive" />
      </div>
      <h2 className="text-lg font-semibold text-foreground mb-2">Something went wrong</h2>
      <p className="text-sm text-muted-foreground max-w-sm mb-6">
        {error.message || "An unexpected error occurred. Please try again."}
      </p>
      <div className="flex items-center gap-3">
        <Button variant="outline" onClick={reset}>
          <RotateCcw className="size-4 mr-2" />
          Try Again
        </Button>
        <Button asChild>
          <Link href="/">
            <Home className="size-4 mr-2" />
            Dashboard
          </Link>
        </Button>
      </div>
    </div>
  );
}
