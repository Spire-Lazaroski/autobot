import { Panel } from "../components/ui";

/** The operating procedure (docs/SOP.html), generated from the code and config by
 *  scripts/build_sop.py. The same document is available as PDF and Markdown. */
export function Help() {
  return (
    <div className="flex flex-col gap-3">
      <Panel title="Operating procedure" right={
        <span className="flex gap-3 text-[12px]">
          <a className="underline" href="/docs/SOP.html" target="_blank" rel="noreferrer"><i className="bi bi-box-arrow-up-right" /> open</a>
          <a className="underline" href="/docs/SOP.pdf" target="_blank" rel="noreferrer"><i className="bi bi-filetype-pdf" /> PDF</a>
        </span>}>
        <p className="text-[12px] text-muted mb-2">
          How to start, watch and stop the bot, what each strategy does, and what to do when something looks wrong.
          Regenerate it after changing strategies or limits: <code className="num">python scripts/build_sop.py --shots</code>.
        </p>
        <iframe title="Operating procedure" src="/docs/SOP.html" className="w-full rounded-lg border border-line" style={{ height: "calc(100vh - 230px)", minHeight: 480 }} />
      </Panel>
    </div>
  );
}
