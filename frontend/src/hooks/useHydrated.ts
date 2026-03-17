"use client";

import { useSyncExternalStore } from "react";

const noop = () => () => {};
const getTrue = () => true;
const getFalse = () => false;

/**
 * Returns `true` on the client after hydration, `false` during SSR.
 * Uses useSyncExternalStore to avoid the setState-in-effect pattern.
 */
export function useHydrated(): boolean {
  return useSyncExternalStore(noop, getTrue, getFalse);
}
