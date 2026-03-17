"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  FileText,
  PenLine,
  ShieldCheck,
  Sun,
  Moon,
  Monitor,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useLayoutStore } from "@/stores/layout-store";
import { cn } from "@/lib/utils";

const BOTTOM_TABS = [
  { href: "/", label: "Home", icon: LayoutDashboard },
  { href: "/documents", label: "Docs", icon: FileText },
  { href: "/review", label: "Review", icon: PenLine },
  { href: "/compliance", label: "Comply", icon: ShieldCheck },
];

const SHEET_LINKS = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/documents", label: "Documents", icon: FileText },
  { href: "/review", label: "Page Review", icon: PenLine },
  { href: "/compliance", label: "Compliance", icon: ShieldCheck },
];

export function MobileBottomNav() {
  const pathname = usePathname();

  return (
    <nav className="fixed bottom-0 left-0 right-0 z-40 h-16 border-t border-border bg-background md:hidden">
      <div className="flex items-center justify-around h-full px-2">
        {BOTTOM_TABS.map((tab) => {
          const isActive =
            tab.href === "/" ? pathname === "/" : pathname.startsWith(tab.href);
          const Icon = tab.icon;
          return (
            <Link
              key={tab.href}
              href={tab.href}
              className={cn(
                "flex flex-col items-center justify-center gap-0.5 min-w-[56px] py-1 rounded-lg transition-colors",
                isActive
                  ? "text-primary"
                  : "text-muted-foreground",
              )}
            >
              <Icon className="size-5" strokeWidth={isActive ? 2.25 : 1.75} />
              <span className="text-[10px] font-medium">{tab.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

export function MobileNavSheet() {
  const pathname = usePathname();
  const { mobileNavOpen, setMobileNavOpen } = useLayoutStore();
  const { theme, setTheme } = useTheme();

  return (
    <Sheet open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
      <SheetContent side="left" className="w-72 p-0 flex flex-col">
        <SheetHeader className="h-12 flex flex-row items-center gap-3 px-4 border-b border-border flex-shrink-0">
          <div className="size-8 rounded-lg bg-gradient-to-br from-primary to-primary/80 flex items-center justify-center shadow-sm">
            <FileText className="size-4 text-primary-foreground" strokeWidth={2.5} />
          </div>
          <SheetTitle className="text-sm font-semibold">AutoTranscript</SheetTitle>
        </SheetHeader>
        <nav className="p-3 space-y-1 flex-1 overflow-y-auto">
          {SHEET_LINKS.map((item) => {
            const isActive =
              item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
            const Icon = item.icon;
            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={() => setMobileNavOpen(false)}
                className={cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary/10 text-primary"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent",
                )}
              >
                <Icon className="size-[18px]" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="p-3 border-t border-border flex flex-col gap-1 flex-shrink-0">
          <p className="px-3 mb-1 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
            Theme
          </p>
          <div className="flex gap-1">
            <button
              type="button"
              onClick={() => setTheme("light")}
              className={cn(
                "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors flex-1",
                theme === "light"
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent",
              )}
            >
              <Sun className="size-4" />
              Light
            </button>
            <button
              type="button"
              onClick={() => setTheme("dark")}
              className={cn(
                "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors flex-1",
                theme === "dark"
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent",
              )}
            >
              <Moon className="size-4" />
              Dark
            </button>
            <button
              type="button"
              onClick={() => setTheme("system")}
              className={cn(
                "flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors flex-1",
                theme === "system"
                  ? "bg-primary/10 text-primary"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent",
              )}
            >
              <Monitor className="size-4" />
              System
            </button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
