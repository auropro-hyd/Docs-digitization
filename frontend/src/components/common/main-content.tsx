"use client";

import { useLayoutStore } from "@/stores/layout-store";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { useHydrated } from "@/hooks/useHydrated";
import { cn } from "@/lib/utils";

export function MainContent({ children }: { children: React.ReactNode }) {
  const isDesktop = useIsDesktop();
  const sidebarCollapsed = useLayoutStore((s) => s.sidebarCollapsed);
  const hydrated = useHydrated();

  const marginClass = !hydrated
    ? "md:ml-16 lg:ml-60"
    : isDesktop && !sidebarCollapsed
      ? "md:ml-60"
      : "md:ml-16";

  return (
    <div className={cn(marginClass, "min-h-screen transition-[margin] duration-200 ease-in-out pb-16 md:pb-0")}>
      {children}
    </div>
  );
}
