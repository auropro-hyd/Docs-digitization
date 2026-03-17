"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import {
  FileText,
  PenLine,
  ShieldCheck,
  LayoutDashboard,
  ChevronsLeft,
  ChevronsRight,
  Sun,
  Moon,
  Settings,
  LogOut,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { useLayoutStore } from "@/stores/layout-store";
import { useIsDesktop } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import { useEffect, type ReactNode } from "react";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Workspace",
    items: [
      { href: "/", label: "Dashboard", icon: <LayoutDashboard className="size-[18px]" /> },
      { href: "/documents", label: "Documents", icon: <FileText className="size-[18px]" /> },
    ],
  },
  {
    title: "Review",
    items: [
      { href: "/review", label: "Page Review", icon: <PenLine className="size-[18px]" /> },
      { href: "/compliance", label: "Compliance", icon: <ShieldCheck className="size-[18px]" /> },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();
  const isDesktop = useIsDesktop();
  const { sidebarCollapsed, toggleSidebar } = useLayoutStore();

  const collapsed = !isDesktop || sidebarCollapsed;
  const width = collapsed ? "w-16" : "w-60";

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "b" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        toggleSidebar();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [toggleSidebar]);

  return (
    <aside
      className={cn(
        "fixed left-0 top-0 bottom-0 z-40 flex-col border-r border-border bg-sidebar transition-[width] duration-200 ease-in-out hidden md:flex",
        width,
      )}
    >
      {/* Logo */}
      <div className="h-12 flex items-center gap-3 px-4 border-b border-sidebar-border flex-shrink-0 overflow-hidden">
        <div className="size-8 rounded-lg bg-gradient-to-br from-primary to-primary/80 flex items-center justify-center shadow-sm flex-shrink-0">
          <FileText className="size-4 text-primary-foreground" strokeWidth={2.5} />
        </div>
        {!collapsed && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="min-w-0"
          >
            <span className="text-sm font-semibold text-sidebar-foreground tracking-tight">AutoTranscript</span>
            <span className="block text-[10px] text-muted-foreground -mt-0.5">Document Intelligence</span>
          </motion.div>
        )}
      </div>

      {/* Nav sections */}
      <nav className="flex-1 overflow-y-auto py-4 px-2">
        {NAV_SECTIONS.map((section) => (
          <div key={section.title} className="mb-6">
            {!collapsed && (
              <p className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                {section.title}
              </p>
            )}
            <div className="space-y-0.5">
              {section.items.map((item) => {
                const isActive =
                  item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);

                const linkContent = (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      "flex items-center gap-3 rounded-lg text-[13px] font-medium transition-all duration-150 group relative",
                      collapsed ? "justify-center px-2 py-2.5" : "px-3 py-2",
                      isActive
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:text-sidebar-foreground hover:bg-accent",
                    )}
                  >
                    {isActive && (
                      <motion.div
                        layoutId="active-nav"
                        className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-primary"
                        transition={{ type: "spring", stiffness: 300, damping: 25 }}
                      />
                    )}
                    <span
                      className={cn(
                        "flex-shrink-0",
                        isActive ? "text-primary" : "text-muted-foreground group-hover:text-sidebar-foreground",
                      )}
                    >
                      {item.icon}
                    </span>
                    {!collapsed && <span>{item.label}</span>}
                  </Link>
                );

                if (collapsed) {
                  return (
                    <Tooltip key={item.href} delayDuration={0}>
                      <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
                      <TooltipContent side="right" sideOffset={8}>
                        {item.label}
                      </TooltipContent>
                    </Tooltip>
                  );
                }
                return <div key={item.href}>{linkContent}</div>;
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Footer */}
      <div className="border-t border-sidebar-border flex-shrink-0 p-2">
        {/* Collapse toggle (desktop only) */}
        {isDesktop && (
          <button
            onClick={toggleSidebar}
            className="w-full flex items-center justify-center gap-2 px-2 py-1.5 rounded-lg text-muted-foreground hover:text-sidebar-foreground hover:bg-accent transition-colors text-xs mb-2"
          >
            {collapsed ? <ChevronsRight className="size-4" /> : <ChevronsLeft className="size-4" />}
            {!collapsed && <span>Collapse</span>}
          </button>
        )}

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              className={cn(
                "w-full flex items-center gap-3 rounded-lg hover:bg-accent transition-colors",
                collapsed ? "justify-center p-2" : "p-2",
              )}
            >
              <Avatar className="size-8 flex-shrink-0">
                <AvatarFallback className="bg-primary/10 text-primary text-xs font-medium">
                  AT
                </AvatarFallback>
              </Avatar>
              {!collapsed && (
                <div className="flex-1 min-w-0 text-left">
                  <p className="text-xs font-medium text-sidebar-foreground truncate">Admin User</p>
                  <p className="text-[10px] text-muted-foreground">Enterprise</p>
                </div>
              )}
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align={collapsed ? "center" : "end"} side="top" className="w-48">
            <DropdownMenuItem onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun className="size-4 mr-2" /> : <Moon className="size-4 mr-2" />}
              {theme === "dark" ? "Light mode" : "Dark mode"}
            </DropdownMenuItem>
            <DropdownMenuItem>
              <Settings className="size-4 mr-2" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem>
              <LogOut className="size-4 mr-2" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </aside>
  );
}
