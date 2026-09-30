import { useEffect, useRef, useState } from "react";

/** Subscribe to /ws/live (pushes a snapshot every 3 s). Reconnects after drops. */
export function useLiveSocket<T>() {
  const [data, setData] = useState<T | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastAt, setLastAt] = useState<number | null>(null);
  const retry = useRef<number | undefined>(undefined);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let closed = false;
    const connect = () => {
      ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/live`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          setData(JSON.parse(e.data));
          setLastAt(Date.now());
        } catch {
          /* ignore a malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry.current = window.setTimeout(connect, 2000);
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry.current);
      ws?.close();
    };
  }, []);

  return { data, connected, lastAt };
}
