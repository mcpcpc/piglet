// Pure helpers for provider credential status: the wording shown in lists, pages and toasts.

import type { CredentialSource, ProviderAuth } from "../protocol/types";

/** The credential piglet uses for `p`, or null when it has none. */
export function inUse(p: ProviderAuth): CredentialSource | null {
  return p.sources[0] ?? null;
}

export function configured(p: ProviderAuth): boolean {
  return p.sources.length > 0;
}

/** Whether `p` has a saved credential that works as is. */
export function savedAndWorking(p: ProviderAuth, type: "api_key" | "oauth"): boolean {
  const source = inUse(p);
  return source?.kind === "stored" && source.type === type && source.problem === null;
}

/** "subscription" or "account": how a provider's sign-in is described. */
export function methodTag(p: ProviderAuth): string {
  return p.login?.subscription ? "subscription" : "account";
}

/** How `p` can be configured, for list rows of unconfigured providers. */
export function methodsText(p: ProviderAuth): string {
  const login = p.login ? `${p.login.account} ${methodTag(p)}` : null;
  if (login && p.keyLabel) return `${login} or API key`;
  return login ?? (p.keyLabel ? "API key" : "");
}

/** Join env var names as "A", "A or B", or "A, B or C". */
export function joinOr(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} or ${items.at(-1)}`;
}

/** The status line for a source: "✓ API key saved", "⚠ Sign-in expired", "Not configured". */
export function statusText(
  p: ProviderAuth,
  source: CredentialSource | null = inUse(p),
  detailed = false,
): { tone: "ok" | "warn" | "none"; text: string } {
  if (!source) return { tone: "none", text: "Not configured" };
  if (source.problem === "expired") return { tone: "warn", text: "Sign-in expired" };
  if (source.problem === "unresolved")
    return { tone: "warn", text: `$${source.missingVar} isn't set` };
  if (source.problem === "blank") return { tone: "warn", text: "Saved key is empty" };
  if (source.problem === "invalid") return { tone: "warn", text: "Saved credential isn't valid" };
  if (source.kind === "env") return { tone: "ok", text: `From ${source.envVar}` };
  if (source.kind === "models.json") return { tone: "ok", text: "From models.json" };
  if (source.type === "api_key") return { tone: "ok", text: "API key saved" };
  return {
    tone: "ok",
    text: detailed && p.login ? `Signed in with ${p.login.account}` : "Signed in",
  };
}

/** The sentence explaining a problem, or where a working credential is set. */
export function explanation(source: CredentialSource): string {
  if (source.problem === "expired") return "This sign-in has expired and piglet can't renew it.";
  if (source.problem === "unresolved") {
    const owner = source.kind === "models.json" ? "models.json's apiKey" : "The saved key";
    return `${owner} reads $${source.missingVar}, which isn't set in piglet's environment.`;
  }
  if (source.problem === "blank") return "The saved key is empty. Replace it or remove it.";
  if (source.problem === "invalid")
    return "The saved credential isn't in a format piglet understands. Replace it or remove it.";
  if (source.kind === "env")
    return "Set in piglet's environment. To change it, update it where piglet is launched.";
  if (source.kind === "models.json") return "Set by apiKey in models.json.";
  return "Saved in piglet's auth.json.";
}

/** A short label for a source, as used in "Also set but not used: …". */
export function sourceLabel(source: CredentialSource): string {
  if (source.kind === "env") return source.envVar;
  if (source.kind === "models.json") return "models.json";
  return source.type === "oauth" ? "your sign-in" : "the saved key";
}

/** "Also set but not used: models.json, GROQ_API_KEY. The saved key takes priority." */
export function shadowedText(p: ProviderAuth): string | null {
  const [winner, ...rest] = p.sources;
  if (!winner || rest.length === 0) return null;
  const label = sourceLabel(winner);
  const who =
    winner.kind === "stored" ? label.charAt(0).toUpperCase() + label.slice(1) : "models.json";
  return `Also set but not used: ${rest.map(sourceLabel).join(", ")}. ${who} takes priority.`;
}

/** What piglet uses after the saved credential is removed. */
export function fallbackAfterRemoval(p: ProviderAuth): CredentialSource | null {
  return inUse(p)?.kind === "stored" ? (p.sources[1] ?? null) : null;
}

/** The consequence line of a remove or sign-out confirmation. */
export function removalConsequence(p: ProviderAuth): string {
  const next = fallbackAfterRemoval(p);
  if (next?.kind === "env") return `piglet will then use ${next.envVar}.`;
  if (next?.kind === "models.json") return "piglet will then use the key in models.json.";
  return `${p.name} won't be usable until you add a key or sign in again.`;
}

/** Whether every word of `query` appears in the provider's name, id, labels or keywords. */
export function matches(p: ProviderAuth, query: string): boolean {
  const haystack = [
    p.name,
    p.id,
    p.keyLabel ?? "",
    p.login ? `${p.login.account} ${methodTag(p)}` : "",
    ...p.keywords,
  ]
    .join(" ")
    .toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((token) => haystack.includes(token));
}

/** The one provider `query` names (by id, by name, or as the only search match), if any. */
export function findProvider(providers: ProviderAuth[], query: string): ProviderAuth | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  const exact = providers.find((p) => p.id === q || p.name.toLowerCase() === q);
  if (exact) return exact;
  const found = providers.filter((p) => matches(p, q));
  return found.length === 1 ? (found[0] ?? null) : null;
}

/** A user-facing reason for a failed request. */
export function reasonText(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message === "Not connected" ? "piglet isn't connected. Try again in a moment." : message;
}
