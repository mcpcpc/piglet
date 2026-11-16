// Composer autocomplete: which token is being completed and how to apply a choice.

export interface CompletionToken {
  kind: "command" | "file";
  query: string;
  start: number;
  end: number;
}

/** Find a `/command` (first token only) or `@path` token ending at the cursor. */
export function tokenAt(text: string, cursor: number): CompletionToken | null {
  const before = text.slice(0, cursor);
  const command = /^\/(\S*)$/.exec(before);
  if (command) return { kind: "command", query: command[1] ?? "", start: 0, end: cursor };
  const file = /(^|\s)@(\S*)$/.exec(before);
  if (file) {
    const query = file[2] ?? "";
    return { kind: "file", query, start: cursor - query.length - 1, end: cursor };
  }
  return null;
}

export function applyCompletion(
  text: string,
  token: CompletionToken,
  value: string,
): { text: string; cursor: number } {
  const insert = token.kind === "command" ? `/${value} ` : `@${value} `;
  const next = text.slice(0, token.start) + insert + text.slice(token.end);
  return { text: next, cursor: token.start + insert.length };
}

export function matchCommand(name: string, query: string): number | null {
  const n = name.toLowerCase();
  const q = query.toLowerCase();
  if (!q) return 0;
  if (n.startsWith(q)) return 0;
  if (n.includes(q)) return 1;
  return null;
}
