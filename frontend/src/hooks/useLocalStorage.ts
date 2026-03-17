"use client";

import { useState, useCallback, useEffect, useRef } from "react";

export function useLocalStorage<T>(key: string, initialValue: T) {
  // Always start with initialValue so server and client match during hydration.
  // Real localStorage value is applied in the mount effect.
  const [storedValue, setStoredValue] = useState<T>(initialValue);
  const initialized = useRef(false);

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      try {
        const raw = window.localStorage.getItem(key);
        if (raw !== null) {
          setStoredValue(JSON.parse(raw) as T);
        }
      } catch {}
    }
  }, [key]);

  const setValue = useCallback(
    (value: T | ((val: T) => T)) => {
      setStoredValue((prev) => {
        const next = value instanceof Function ? value(prev) : value;
        try {
          window.localStorage.setItem(key, JSON.stringify(next));
        } catch {}
        return next;
      });
    },
    [key],
  );

  const removeValue = useCallback(() => {
    try {
      window.localStorage.removeItem(key);
    } catch {}
    setStoredValue(initialValue);
  }, [key, initialValue]);

  return [storedValue, setValue, removeValue] as const;
}
