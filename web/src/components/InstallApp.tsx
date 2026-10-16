import { useEffect, useState } from "react";
import { Confirm } from "./Confirm";

interface PromptEvent extends Event { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> }

const standalone = () => window.matchMedia("(display-mode: standalone)").matches || (navigator as unknown as { standalone?: boolean }).standalone === true;
const ios = () => /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

/** "Install app" in the header: Android, Chrome and Edge get the browser's own install prompt; iPhone and iPad get
 *  the two Safari steps (Apple has no install prompt). Hidden inside the installed app. */
export function InstallApp() {
  const [evt, setEvt] = useState<PromptEvent | null>(null);
  const [help, setHelp] = useState(false);
  const [done, setDone] = useState(standalone());
  useEffect(() => {
    const onPrompt = (e: Event) => { e.preventDefault(); setEvt(e as PromptEvent); };
    const onInstalled = () => { setDone(true); setEvt(null); };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => { window.removeEventListener("beforeinstallprompt", onPrompt); window.removeEventListener("appinstalled", onInstalled); };
  }, []);
  if (done || (!evt && !ios())) return null;
  return (
    <>
      <a href="#" title="Add Spire Analytics to this device's home screen" onClick={async (e) => {
        e.preventDefault();
        if (evt) { await evt.prompt(); const c = await evt.userChoice; if (c.outcome === "accepted") setDone(true); setEvt(null); }
        else setHelp(true);
      }}><i className="bi bi-phone" /> Install app</a>
      {help && (
        <Confirm title="Install on iPhone or iPad" confirmLabel="Got it" onConfirm={() => setHelp(false)} onCancel={() => setHelp(false)}>
          <ol className="list-decimal pl-5 flex flex-col gap-1.5">
            <li>In <b className="text-fg">Safari</b>, tap the <b className="text-fg">Share</b> button <i className="bi bi-box-arrow-up" /> at the bottom.</li>
            <li>Choose <b className="text-fg">Add to Home Screen</b>, then <b className="text-fg">Add</b>.</li>
          </ol>
          <p className="mt-2">Spire Analytics then opens full-screen from its icon. Sign in once with "keep me signed in".</p>
        </Confirm>)}
    </>
  );
}

/** Register the service worker (production build only: Vite's dev server doesn't need it). */
export function registerServiceWorker() {
  if (import.meta.env.PROD && "serviceWorker" in navigator) {
    window.addEventListener("load", () => { navigator.serviceWorker.register("/sw.js").catch(() => undefined); });
  }
}
