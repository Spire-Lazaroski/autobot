import { useState } from "react";

// Page state that survives switching tabs (pages unmount) but not a reload.
const store = new Map<string, unknown>();

export function usePageState<T>(key: string, init: T): [T, (v: T) => void] {
  const [v, setV] = useState<T>(() => (store.has(key) ? (store.get(key) as T) : init));
  return [v, (next: T) => { store.set(key, next); setV(next); }];
}
