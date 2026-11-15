// Pure helpers over session entries: branch paths, siblings and tool-result lookup.

import type { AgentMessage, SessionEntry, ToolResultMessage } from "../protocol/types";

export function indexEntries(entries: SessionEntry[]): Map<string, SessionEntry> {
  return new Map(entries.map((entry) => [entry.id, entry]));
}

/** Entries from the root to `leafId` (inclusive). */
export function branchPath(entries: SessionEntry[], leafId: string | null): SessionEntry[] {
  if (leafId === null) return [];
  const byId = indexEntries(entries);
  const path: SessionEntry[] = [];
  const seen = new Set<string>();
  let current = byId.get(leafId);
  while (current && !seen.has(current.id)) {
    seen.add(current.id);
    path.push(current);
    current = current.parentId ? byId.get(current.parentId) : undefined;
  }
  return path.reverse();
}

export function childrenOf(entries: SessionEntry[], parentId: string | null): SessionEntry[] {
  return entries.filter((entry) => entry.parentId === parentId && entry.type !== "label");
}

export function isUserEntry(entry: SessionEntry): boolean {
  return entry.type === "message" && entry.message.role === "user";
}

/** User-message siblings of `entry` (including itself), oldest first. */
export function userSiblings(entries: SessionEntry[], entry: SessionEntry): SessionEntry[] {
  return childrenOf(entries, entry.parentId).filter(isUserEntry);
}

/** The most recent leaf under `entry` (following the newest child at each step). */
export function latestLeaf(entries: SessionEntry[], entry: SessionEntry): SessionEntry {
  let current = entry;
  for (;;) {
    const newest = childrenOf(entries, current.id).at(-1);
    if (!newest) return current;
    current = newest;
  }
}

export function toolResultsById(messages: AgentMessage[]): Map<string, ToolResultMessage> {
  const results = new Map<string, ToolResultMessage>();
  for (const message of messages) {
    if (message.role === "toolResult") results.set(message.toolCallId, message);
  }
  return results;
}

export function labelsByTarget(entries: SessionEntry[]): Map<string, string> {
  const labels = new Map<string, string>();
  for (const entry of entries) {
    if (entry.type !== "label") continue;
    if (entry.label) labels.set(entry.targetId, entry.label);
    else labels.delete(entry.targetId);
  }
  return labels;
}

export type MessageContent = string | readonly { type: string; text?: string }[];

export function messageText(content: MessageContent | undefined): string {
  if (content === undefined) return "";
  if (typeof content === "string") return content;
  return content
    .map((block) => (block.type === "text" ? (block.text ?? "") : ""))
    .filter(Boolean)
    .join("");
}
