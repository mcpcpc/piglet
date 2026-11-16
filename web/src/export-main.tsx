// The standalone HTML export viewer: renders the embedded session read-only.

import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { latestLeaf } from "./lib/tree";
import type { SessionEntry, SessionHeader } from "./protocol/types";
import { Transcript } from "./transcript/Transcript";
import "./styles.css";

interface ExportData {
  header: SessionHeader;
  entries: SessionEntry[];
  leafId: string | null;
}

function readData(): ExportData | null {
  const element = document.getElementById("piglet-session");
  if (!element?.textContent) return null;
  return JSON.parse(element.textContent) as ExportData;
}

function Viewer({ data }: { data: ExportData }) {
  const [leafId, setLeafId] = useState(data.leafId);
  const name = data.entries.findLast((e) => e.type === "session_info");
  const title = name?.type === "session_info" && name.name ? name.name : "piglet session";
  return (
    <div className="mx-auto max-w-3xl px-5 py-8">
      <header className="mb-6 border-b border-line pb-4">
        <h1 className="text-[18px] font-semibold">{title}</h1>
        <p className="mt-1 font-mono text-[12px] text-fg-muted">
          {data.header.cwd} · {new Date(data.header.timestamp).toLocaleString()}
        </p>
      </header>
      <Transcript
        entries={data.entries}
        leafId={leafId}
        cwd={data.header.cwd}
        actions={{
          switchTo: (entryId) => {
            const entry = data.entries.find((e) => e.id === entryId);
            if (entry) setLeafId(latestLeaf(data.entries, entry).id);
          },
        }}
      />
    </div>
  );
}

const root = document.getElementById("root");
const data = readData();
if (root && data) {
  document.title = "piglet session";
  createRoot(root).render(
    <StrictMode>
      <Viewer data={data} />
    </StrictMode>,
  );
}
