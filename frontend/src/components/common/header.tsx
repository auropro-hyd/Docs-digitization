"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { Search, Bell, Menu } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { useLayoutStore } from "@/stores/layout-store";
import { Suspense } from "react";

const ROUTE_LABELS: Record<string, string> = {
  "/": "Dashboard",
  "/documents": "Documents",
  "/review": "Page Review",
  "/compliance": "Compliance",
};

function HeaderBreadcrumbs() {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const docId = searchParams.get("doc");
  const label = ROUTE_LABELS[pathname] || pathname.slice(1);

  return (
    <Breadcrumb>
      <BreadcrumbList>
        {pathname !== "/" && (
          <>
            <BreadcrumbItem>
              <BreadcrumbLink href="/">Dashboard</BreadcrumbLink>
            </BreadcrumbItem>
            <BreadcrumbSeparator />
          </>
        )}
        <BreadcrumbItem>
          <BreadcrumbPage>{label}</BreadcrumbPage>
        </BreadcrumbItem>
        {docId && (
          <>
            <BreadcrumbSeparator />
            <BreadcrumbItem>
              <BreadcrumbPage className="font-mono text-xs">{docId.slice(0, 8)}...</BreadcrumbPage>
            </BreadcrumbItem>
          </>
        )}
      </BreadcrumbList>
    </Breadcrumb>
  );
}

export function Header() {
  const { setCommandPaletteOpen, setMobileNavOpen } = useLayoutStore();

  return (
    <header className="sticky top-0 z-30 h-12 border-b border-border bg-background/80 backdrop-blur-sm flex items-center justify-between px-4 lg:px-6">
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          size="icon"
          className="md:hidden size-8"
          onClick={() => setMobileNavOpen(true)}
        >
          <Menu className="size-4" />
        </Button>
        <Suspense fallback={<div className="h-4 w-32 bg-muted animate-pulse rounded" />}>
          <HeaderBreadcrumbs />
        </Suspense>
      </div>

      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          className="hidden sm:flex items-center gap-2 text-muted-foreground h-8 px-3"
          onClick={() => setCommandPaletteOpen(true)}
        >
          <Search className="size-3.5" />
          <span className="text-xs">Search...</span>
          <kbd className="pointer-events-none ml-2 hidden lg:inline-flex h-5 select-none items-center gap-0.5 rounded border border-border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
            ⌘K
          </kbd>
        </Button>
        <Button variant="ghost" size="icon" className="size-8 text-muted-foreground sm:hidden" onClick={() => setCommandPaletteOpen(true)}>
          <Search className="size-4" />
        </Button>
        <Button variant="ghost" size="icon" className="size-8 text-muted-foreground">
          <Bell className="size-4" />
        </Button>
      </div>
    </header>
  );
}
