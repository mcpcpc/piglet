// Model picker helpers: identity keys, the per-browser "recent models" list and display names.

/** A model's identity across providers, as `provider/id`. */
export function modelKey(model: { provider: string; id: string }): string {
  return `${model.provider}/${model.id}`;
}

const RECENT_KEY = "piglet:recent-models";
const RECENT_LIMIT = 5;

/** Keys of the models most recently chosen in this browser, newest first. */
export function recentModels(): string[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((k): k is string => typeof k === "string") : [];
  } catch {
    return [];
  }
}

/** Put `key` at the front of the recent list (a per-viewer convenience; storage may be off). */
export function rememberModel(key: string): void {
  const next = [key, ...recentModels().filter((k) => k !== key)].slice(0, RECENT_LIMIT);
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {
    // Without storage the Recent group just stays empty.
  }
}

/** Drop the "Vendor: " prefix some catalogs put on names: "Anthropic: Claude Sonnet 4.5". */
export function shortModelName(name: string): string {
  return /^[^:]{1,40}:\s+(\S.*)$/.exec(name)?.[1] ?? name;
}
