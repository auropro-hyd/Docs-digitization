import { create } from "zustand";

function getInitialCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const stored = window.localStorage.getItem("sidebar-collapsed");
    return stored === "true";
  } catch {
    return false;
  }
}

interface LayoutState {
  sidebarCollapsed: boolean;
  commandPaletteOpen: boolean;
  mobileNavOpen: boolean;

  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setCommandPaletteOpen: (open: boolean) => void;
  setMobileNavOpen: (open: boolean) => void;
}

export const useLayoutStore = create<LayoutState>((set) => ({
  sidebarCollapsed: getInitialCollapsed(),
  commandPaletteOpen: false,
  mobileNavOpen: false,

  toggleSidebar: () =>
    set((s) => {
      const next = !s.sidebarCollapsed;
      try {
        window.localStorage.setItem("sidebar-collapsed", String(next));
      } catch {}
      return { sidebarCollapsed: next };
    }),
  setSidebarCollapsed: (collapsed) => {
    try {
      window.localStorage.setItem("sidebar-collapsed", String(collapsed));
    } catch {}
    set({ sidebarCollapsed: collapsed });
  },
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  setMobileNavOpen: (open) => set({ mobileNavOpen: open }),
}));
