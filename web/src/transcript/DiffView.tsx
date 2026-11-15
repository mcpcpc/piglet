import { parseUnifiedDiff } from "../lib/diff";

const ROW: Record<string, string> = {
  add: "bg-add-bg text-add-fg",
  del: "bg-del-bg text-del-fg",
  context: "",
  hunk: "text-fg-faint",
};

export function DiffView({ diff, maxLines }: { diff: string; maxLines?: number }) {
  const lines = parseUnifiedDiff(diff);
  const shown = maxLines ? lines.slice(0, maxLines) : lines;
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-code font-mono text-[12px] leading-[1.55]">
      <table className="w-full border-collapse">
        <tbody>
          {shown.map((line, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: diff lines have no stable id
            <tr key={index} className={ROW[line.kind]}>
              <td className="w-10 select-none px-2 text-right text-fg-faint">
                {line.kind === "hunk" ? "" : (line.oldLine ?? "")}
              </td>
              <td className="w-10 select-none px-2 text-right text-fg-faint">
                {line.kind === "hunk" ? "" : (line.newLine ?? "")}
              </td>
              <td className="w-4 select-none text-center">
                {line.kind === "add" ? "+" : line.kind === "del" ? "−" : ""}
              </td>
              <td className="whitespace-pre pr-3">{line.text}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {maxLines && lines.length > maxLines ? (
        <div className="px-3 py-1 text-fg-faint">… {lines.length - maxLines} more lines</div>
      ) : null}
    </div>
  );
}
