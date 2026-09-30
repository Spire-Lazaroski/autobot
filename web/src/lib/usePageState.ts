import { useState } from "react";

// Page state that survives switching tabs (pages unmount) but not a reload.
const store = new Map<string, unknown>();

export function usePageState<T>(key: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => (store.has(key) ? (store.get(key) as T) : init));
  return [v, (next: T) => { store.set(key, next); setV(next); }];
}

/** Set another page's state before navigating there (e.g. "walk-forward this"). */
export function setPageState<T>(key: string, v: T) {
  store.set(key, v);
}

export function getPageState<T>(key: string): T | undefined {
  return store.get(key) as T | undefined;
}
