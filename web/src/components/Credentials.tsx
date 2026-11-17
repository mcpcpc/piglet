// Provider credentials: the provider list and the per-provider page that Settings → Providers
// and the /login dialog share. Every sign-in, key change and removal happens on the page.

import { Command } from "cmdk";
import {
  type FormEvent,
  type KeyboardEvent,
  type MutableRefObject,
  type ReactElement,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { COARSE_POINTER, useMediaQuery } from "../lib/media";
import { isElectron, openExternal } from "../lib/native";
import {
  configured,
  explanation,
  inUse,
  joinOr,
  methodsText,
  reasonText,
  removalConsequence,
  shadowedText,
  statusText,
} from "../lib/providers";
import type { CredentialSource, ProviderAuth } from "../protocol/types";
import {
  cancelLogin,
  type LoginState,
  NEW_ACCOUNT,
  refreshProviders,
  removeCredential,
  respondLogin,
  saveApiKey,
  startLogin,
  toast,
  ui,
  useApp,
} from "../state/store";
import { Icon, Spinner } from "./Icon";
import { Button, buttonClass, inputClass } from "./Modal";

/** 44px buttons, links and fields on touch screens (Apple's minimum touch target). */
export const TOUCH_TARGETS =
  "pointer-coarse:[&_a]:min-h-11 pointer-coarse:[&_button]:min-h-11 pointer-coarse:[&_input]:min-h-11";

/** A handler the dialog calls on Esc; it returns true when it closed something itself. */
export type EscapeRef = MutableRefObject<(() => boolean) | null>;

const ANTHROPIC_NOTE =
  "Claude subscriptions can't be used in third-party apps, so Anthropic needs an API key.";

/** "✓ API key saved", "⚠ Sign-in expired", "Not configured" or a neutral "• Signed in". */
export function StatusLine({
  p,
  source = inUse(p),
  detailed = false,
  neutral = false,
}: {
  p: ProviderAuth;
  source?: CredentialSource | null;
  detailed?: boolean;
  neutral?: boolean;
}) {
  const { tone, text } = statusText(p, source, detailed);
  if (tone === "none") return <span className="text-fg-muted">{text}</span>;
  if (neutral)
    return (
      <span className="inline-flex items-baseline gap-1 text-fg-muted">
        <Icon name="dot" size={10} className="shrink-0 self-center" />
        <span className="break-words">{text}</span>
      </span>
    );
  return (
    <span
      className={`inline-flex items-baseline gap-1 ${tone === "ok" ? "text-add-fg" : "text-fg"}`}
    >
      <Icon
        name={tone === "ok" ? "check" : "alert"}
        size={12}
        className={`shrink-0 self-center ${tone === "warn" ? "text-warning" : ""}`}
      />
      <span className="min-w-0 break-words">{text}</span>
    </span>
  );
}

/** Second line of a list row: the status, or how an unconfigured provider can be set up. */
export function rowStatus(p: ProviderAuth): ReactNode {
  return configured(p) ? (
    <StatusLine p={p} />
  ) : (
    <span className="text-fg-muted">{methodsText(p)}</span>
  );
}

// The keyboard highlight shows only while the list has focus, so nothing looks stuck on touch.
const rowClass =
  "flex cursor-pointer items-center gap-3 px-3 py-2 text-[13px] hover:bg-hover group-focus-within:data-[selected=true]:bg-hover pointer-coarse:min-h-11";

const TYPE_AHEAD_MS = 700;

function ListGroup({
  heading,
  rows,
  line2,
  onOpen,
}: {
  heading: string | null;
  rows: ProviderAuth[];
  line2: (p: ProviderAuth) => ReactNode;
  onOpen: (p: ProviderAuth) => void;
}) {
  const headingId = useId();
  if (rows.length === 0) return null;
  return (
    <fieldset aria-labelledby={heading ? headingId : undefined} className="mb-3 min-w-0">
      {heading ? (
        <div
          id={headingId}
          className="mb-1 text-[12px] font-medium uppercase tracking-wide text-fg-muted"
        >
          {heading}
        </div>
      ) : null}
      <div className="divide-y divide-line overflow-hidden rounded-md border border-line">
        {rows.map((p) => (
          <Command.Item key={p.id} value={p.id} onSelect={() => onOpen(p)} className={rowClass}>
            <span className="min-w-0 flex-1">
              <span
                className="line-clamp-2 block font-medium [overflow-wrap:anywhere]"
                title={p.name}
              >
                {p.name}
              </span>
              <span className="block text-[12px]">{line2(p)}</span>
            </span>
            <Icon name="chevronRight" size={14} className="shrink-0 text-fg-faint" />
          </Command.Item>
        ))}
      </div>
    </fieldset>
  );
}

/**
 * The provider list: one tab stop, arrow keys and Enter (cmdk), and type-ahead: typing a name
 * jumps to it, and repeating a letter cycles through the names that start with it.
 */
export function ProviderList({
  providers,
  selected,
  onSelected,
  onOpen,
  grouped = false,
  line2 = rowStatus,
  autoFocus = false,
}: {
  providers: ProviderAuth[];
  selected: string;
  onSelected: (id: string) => void;
  onOpen: (p: ProviderAuth) => void;
  grouped?: boolean;
  line2?: (p: ProviderAuth) => ReactNode;
  autoFocus?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const typed = useRef({ text: "", at: 0 });
  const hintId = useId();
  const ready = providers.filter(configured);
  const rest = providers.filter((p) => !configured(p));
  const ordered = grouped ? [...ready, ...rest] : providers;

  useEffect(() => {
    if (autoFocus) root.current?.focus();
  }, [autoFocus]);

  const typeAhead = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key.length !== 1 || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === " ") return;
    const now = Date.now();
    const text =
      now - typed.current.at > TYPE_AHEAD_MS ? event.key : typed.current.text + event.key;
    typed.current = { text, at: now };
    const repeated = [...text].every((ch) => ch === text[0]);
    const prefix = (repeated ? text[0] : text)?.toLowerCase() ?? "";
    const candidates = ordered.filter((p) => p.name.toLowerCase().startsWith(prefix));
    if (candidates.length === 0) return;
    const current = candidates.findIndex((p) => p.id === selected);
    const next =
      repeated && text.length > 1 ? candidates[(current + 1) % candidates.length] : candidates[0];
    if (!next) return;
    event.preventDefault();
    onSelected(next.id);
    root.current
      ?.querySelector(`[cmdk-item][data-value="${CSS.escape(next.id)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  };

  return (
    <Command
      ref={root}
      label="Providers"
      shouldFilter={false}
      loop={false}
      value={selected}
      onValueChange={onSelected}
      tabIndex={0}
      aria-describedby={hintId}
      onKeyDown={typeAhead}
      className="group outline-none"
    >
      <p id={hintId} className="sr-only">
        Use the arrow keys to move and Enter to open. Type a name to jump to it.
      </p>
      <Command.List>
        {providers.length === 0 ? (
          <p className="text-[13px] text-fg-muted">No providers available.</p>
        ) : grouped ? (
          <>
            <ListGroup
              heading={`Configured · ${ready.length}`}
              rows={ready}
              line2={line2}
              onOpen={onOpen}
            />
            <ListGroup
              heading={`Not configured · ${rest.length}`}
              rows={rest}
              line2={line2}
              onOpen={onOpen}
            />
          </>
        ) : (
          <ListGroup heading={null} rows={providers} line2={line2} onOpen={onOpen} />
        )}
      </Command.List>
    </Command>
  );
}

function KeyForm({
  p,
  onCancel,
  onSaved,
}: {
  p: ProviderAuth;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputId = useId();
  const current = inUse(p);
  const signedIn = current?.kind === "stored" && current.type === "oauth";
  const account = p.login?.account ?? p.name;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await saveApiKey(p, value);
      onSaved();
    } catch (err) {
      setError(reasonText(err));
      setBusy(false);
    }
  };
  return (
    <form id={`key-form-${p.id}`} className="space-y-2" onSubmit={(e) => void submit(e)}>
      <label htmlFor={inputId} tabIndex={-1} className="block font-medium outline-none">
        {p.keyLabel}
      </label>
      <div className="flex gap-2 max-sm:flex-col">
        <input
          id={inputId}
          data-autofocus
          type="password"
          autoComplete="off"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="done"
          placeholder="Paste your key"
          className={inputClass}
          value={value}
          disabled={busy}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button type="submit" variant="primary" disabled={!value.trim() || busy}>
          {busy ? "Saving…" : signedIn ? "Save key and sign out" : "Save key"}
        </Button>
        <Button onClick={onCancel} disabled={busy}>
          Cancel
        </Button>
      </div>
      {signedIn ? (
        <p className="flex items-baseline gap-1.5">
          <Icon name="alert" size={12} className="shrink-0 self-center text-warning" />
          Saving a key signs you out of {account}.
        </p>
      ) : null}
      <p className="text-[12px] text-fg-muted">
        You can also enter <code>$ENV_VAR</code> to read a variable, or <code>!command</code> to run
        one (for example, a password manager CLI).
      </p>
      {error ? (
        <p role="alert" className="text-danger">
          Couldn't save the key. {error}
        </p>
      ) : null}
    </form>
  );
}

/** Inline "Remove the saved … API key?" / "Sign out of …?" confirmation. */
export function ConfirmRemove({
  p,
  onCancel,
  onDone,
}: {
  p: ProviderAuth;
  onCancel: () => void;
  onDone: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const questionId = useId();
  const box = useRef<HTMLFieldSetElement>(null);
  const current = inUse(p);
  const oauth = current?.kind === "stored" && current.type === "oauth";
  useEffect(() => box.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus(), []);
  const confirm = async () => {
    setBusy(true);
    try {
      await removeCredential(p);
      onDone();
    } catch (error) {
      toast(`Couldn't remove credentials for ${p.name}. ${reasonText(error)}`, "error");
      setBusy(false);
    }
  };
  return (
    <fieldset
      ref={box}
      aria-labelledby={questionId}
      className="min-w-0 space-y-2 rounded-md border border-line bg-subtle p-3 text-[13px]"
    >
      <p id={questionId} className="font-medium">
        {oauth ? `Sign out of ${p.name}?` : `Remove the saved ${p.name} API key?`}
      </p>
      <p className="text-fg-muted">{removalConsequence(p)}</p>
      <div className="flex gap-2 max-sm:flex-col">
        <Button variant="danger" disabled={busy} onClick={() => void confirm()}>
          {busy ? (oauth ? "Signing out…" : "Removing…") : oauth ? "Sign out" : "Remove key"}
        </Button>
        <Button data-autofocus disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </fieldset>
  );
}

function SignInFlow({
  p,
  login,
  onExit,
}: {
  p: ProviderAuth;
  login: LoginState;
  onExit: () => void;
}) {
  const coarse = useMediaQuery(COARSE_POINTER);
  const [pasted, setPasted] = useState("");
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const pasteRef = useRef<HTMLInputElement>(null);
  const pasteId = useId();
  const current = inUse(p);
  const keySaved = current?.kind === "stored" && current.type === "api_key";

  useEffect(() => {
    if (login.inputError) pasteRef.current?.select();
  }, [login.inputError]);

  if (login.status === "error" || login.status === "cancelled") {
    // A saved account that doesn't match the one the user signed in to: offer to add it.
    const mismatch = login.status === "error" && login.code === "account_mismatch";
    return (
      <div className="space-y-3">
        <p className="flex items-center gap-1.5 font-medium">
          <Icon name="alert" size={14} className="shrink-0 text-warning" />
          {login.status === "cancelled" ? "Sign-in was cancelled" : "Sign-in didn't finish"}
        </p>
        {login.status === "error" && login.message ? (
          <div
            role="alert"
            className="rounded-md border border-danger/40 p-3 [overflow-wrap:anywhere]"
          >
            {login.message}
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2 max-sm:flex-col">
          {mismatch ? (
            <Button
              variant="primary"
              data-autofocus
              onClick={() => void startLogin(p, NEW_ACCOUNT)}
            >
              Use another {login.account} account
            </Button>
          ) : null}
          <Button
            variant={mismatch ? "default" : "primary"}
            data-autofocus={mismatch ? undefined : true}
            onClick={() => void startLogin(p, login.savedAccount?.id)}
          >
            Try again
          </Button>
          <Button onClick={onExit}>Back</Button>
        </div>
      </div>
    );
  }

  const status =
    login.status === "starting"
      ? "Starting sign-in…"
      : login.checking
        ? (login.message ?? "Checking the address…")
        : "Waiting for you to sign in…";
  const url = login.url;
  const copyLink = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopy("copied");
      window.setTimeout(() => setCopy("idle"), 2000);
    } catch {
      setCopy("failed");
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (pasted.trim() && !login.checking) void respondLogin(pasted);
  };
  return (
    <div className="space-y-4">
      <p className="flex items-center gap-2" aria-live="polite">
        <span aria-hidden="true">
          <Spinner />
        </span>
        {status}
      </p>
      {url ? (
        <ol className="space-y-4">
          <li className="flex gap-3">
            <StepNumber n={1} />
            <div className="min-w-0 flex-1 space-y-2">
              <p>
                Open the sign-in page and sign in with {login.account}
                {login.savedAccount ? ` as ${login.savedAccount.label}` : ""}.
              </p>
              <div className="flex gap-2 max-sm:flex-col">
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-autofocus
                  aria-label="Open sign-in page in a new tab"
                  onClick={(e) => {
                    if (!isElectron()) return;
                    e.preventDefault();
                    void openExternal(url);
                  }}
                  className={buttonClass(
                    "primary",
                    "inline-flex items-center justify-center gap-1.5 no-underline",
                  )}
                >
                  Open sign-in page <Icon name="external" size={12} />
                </a>
                <Button onClick={() => void copyLink()}>
                  {copy === "copied" ? "Copied" : "Copy link"}
                </Button>
              </div>
              {copy === "failed" ? (
                <p className="text-fg-muted">Couldn't copy. Use Open sign-in page instead.</p>
              ) : null}
              {login.savedAccount ? (
                <AccountLinks p={p} login={login} current={login.savedAccount.id} />
              ) : null}
            </div>
          </li>
          <li className="flex gap-3">
            <StepNumber n={2} />
            <form className="min-w-0 flex-1 space-y-2" onSubmit={submit}>
              <p>
                {coarse
                  ? "After you sign in, your browser shows a “can't connect” page. That's expected. Copy that page's address, come back to this tab and paste it here."
                  : "If piglet doesn't continue on its own, copy the address your browser ends on and paste it here. It starts with http://127.0.0.1."}
              </p>
              <label htmlFor={pasteId} className="block font-medium">
                Address from your browser
              </label>
              <div className="flex gap-2 max-sm:flex-col">
                <input
                  id={pasteId}
                  ref={pasteRef}
                  inputMode="url"
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  placeholder="http://127.0.0.1…"
                  className={inputClass}
                  value={pasted}
                  disabled={login.checking}
                  onChange={(e) => setPasted(e.target.value)}
                />
                <Button type="submit" disabled={!pasted.trim() || login.checking}>
                  Continue
                </Button>
              </div>
              {login.inputError ? (
                <p role="alert" className="flex items-baseline gap-1.5 text-danger">
                  <Icon name="alert" size={12} className="shrink-0 self-center" />
                  <span className="[overflow-wrap:anywhere]">{login.inputError}</span>
                </p>
              ) : null}
            </form>
          </li>
        </ol>
      ) : null}
      {keySaved ? (
        <p className="text-fg-muted">When you finish, this replaces your saved API key.</p>
      ) : null}
      <Button onClick={onExit} data-autofocus={url ? undefined : true}>
        Cancel sign-in
      </Button>
    </div>
  );
}

/** Switch a reused sign-in to another saved account, or register another account. */
function AccountLinks({
  p,
  login,
  current,
}: {
  p: ProviderAuth;
  login: LoginState;
  current: string;
}) {
  const others = (p.login?.accounts ?? []).filter((a) => a.id !== current);
  const link = "text-left text-accent underline pointer-coarse:min-h-11";
  return (
    <div className="flex flex-col items-start gap-1">
      {others.map((account) => (
        <button
          key={account.id}
          type="button"
          className={link}
          onClick={() => void startLogin(p, account.id)}
        >
          Use {account.label} instead
        </button>
      ))}
      <button type="button" className={link} onClick={() => void startLogin(p, NEW_ACCOUNT)}>
        Use another {login.account} account
      </button>
    </div>
  );
}

function StepNumber({ n }: { n: number }) {
  return (
    <span
      aria-hidden="true"
      className="flex size-5 shrink-0 items-center justify-center rounded-full border border-line text-[11px] text-fg-muted"
    >
      {n}
    </span>
  );
}

type Mode = "overview" | "key" | "confirm";

/**
 * One provider's credentials: status, sources and every action, including the sign-in flow.
 * Mount it with `key={p.id}` so nothing typed for one provider can reach another.
 */
export function ProviderPage({
  p,
  context,
  backLabel,
  onBack,
  onDone,
  onSignInExit,
  startWithKey = false,
  escapeRef,
}: {
  p: ProviderAuth;
  /** In /login a finished action shows a Done view; in Settings it returns to the overview. */
  context: "settings" | "login";
  backLabel: string;
  onBack: () => void;
  onDone?: () => void;
  /** Called when a sign-in is cancelled or abandoned, after it is cancelled. */
  onSignInExit?: () => void;
  startWithKey?: boolean;
  escapeRef: EscapeRef;
}) {
  const login = useApp((s) => (s.login?.provider === p.id ? s.login : null));
  const coarse = useMediaQuery(COARSE_POINTER);
  const keyOnly = !configured(p) && p.keyLabel !== null && p.login === null;
  const [mode, setMode] = useState<Mode>(startWithKey ? "key" : "overview");
  const [done, setDone] = useState<"key" | "signin" | null>(null);
  const root = useRef<HTMLElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const headingId = useId();
  const [focusKey, setFocusKey] = useState(0);
  const signingIn =
    login !== null && login.status !== "success" && !(context === "login" && done !== null);
  const showKeyForm =
    !signingIn && done === null && (mode === "key" || (keyOnly && mode !== "confirm"));

  // A sign-in left running when the page goes away (Back, tab switch, dialog closed) is cancelled.
  useEffect(
    () => () => {
      if (useApp.getState().login?.provider === p.id) void cancelLogin();
    },
    [p.id],
  );

  useEffect(() => {
    if (login?.status !== "success") return;
    if (context === "login") {
      setDone("signin");
    } else {
      void cancelLogin();
      setMode("overview");
    }
    setFocusKey((k) => k + 1);
  }, [login?.status, context]);

  // Move focus to the view's first control whenever the view changes.
  const view = `${mode}:${showKeyForm}:${login?.status ?? ""}:${Boolean(login?.url)}:${done}`;
  // biome-ignore lint/correctness/useExhaustiveDependencies: focus follows the view and focusKey
  useEffect(() => {
    const marked = root.current?.querySelector<HTMLElement>("[data-autofocus]");
    const target =
      marked && !marked.matches("button, a, input")
        ? marked.querySelector<HTMLElement>("button, a, input")
        : marked;
    if (target instanceof HTMLInputElement && coarse) heading.current?.focus();
    else (target ?? heading.current)?.focus();
  }, [view, focusKey]);

  const exitSignIn = () => {
    void cancelLogin();
    setMode("overview");
    setFocusKey((k) => k + 1);
    onSignInExit?.();
  };

  escapeRef.current = () => {
    if (done !== null) return false;
    if (signingIn) {
      exitSignIn();
      return true;
    }
    if (mode === "confirm" || (mode === "key" && !keyOnly)) {
      setMode("overview");
      setFocusKey((k) => k + 1);
      return true;
    }
    return false;
  };

  const backToOverview = () => {
    setMode("overview");
    setFocusKey((k) => k + 1);
  };
  const keySaved = () => {
    if (context === "login") setDone("key");
    else backToOverview();
  };

  const current = inUse(p);
  const showId = p.id !== p.name.toLowerCase();

  let body: ReactNode;
  if (done !== null) {
    body = (
      <div className="space-y-3">
        <p className="text-fg-muted">Saved in piglet's auth.json.</p>
        <Button variant="primary" data-autofocus onClick={onDone}>
          Done
        </Button>
      </div>
    );
  } else if (signingIn && login) {
    body = <SignInFlow p={p} login={login} onExit={exitSignIn} />;
  } else if (showKeyForm) {
    body = <KeyForm p={p} onCancel={keyOnly ? onBack : backToOverview} onSaved={keySaved} />;
  } else if (mode === "confirm") {
    body = <ConfirmRemove p={p} onCancel={backToOverview} onDone={backToOverview} />;
  } else {
    body = <Actions p={p} onKey={() => setMode("key")} onRemove={() => setMode("confirm")} />;
  }

  const notes: ReactNode[] = [];
  if (!signingIn && done === null) {
    if (
      current &&
      (current.kind === "env" || current.kind === "models.json") &&
      mode === "overview"
    ) {
      const over = current.kind === "env" ? current.envVar : "models.json";
      notes.push(
        p.login
          ? `A saved key or sign-in takes priority over ${over}.`
          : `A saved key takes priority over ${over}.`,
      );
    }
    const envSet = p.sources.some((s) => s.kind === "env");
    if (!envSet && p.envVars.length && (showKeyForm || !configured(p)))
      notes.push(
        <>
          Or set <code>{joinOr(p.envVars)}</code> where piglet is launched.
        </>,
      );
    if (p.id === "anthropic") notes.push(ANTHROPIC_NOTE);
  }

  return (
    <section ref={root} aria-labelledby={headingId} className="space-y-3 text-[13px]">
      <Button
        variant="ghost"
        aria-label={backLabel}
        onClick={signingIn ? exitSignIn : onBack}
        className="-ml-2 inline-flex items-center gap-1 text-accent pointer-coarse:min-h-11"
      >
        <Icon name="chevronLeft" size={14} /> Back
      </Button>
      <div className="space-y-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <h3
            id={headingId}
            ref={heading}
            tabIndex={-1}
            className="text-[15px] font-semibold outline-none [overflow-wrap:anywhere]"
          >
            {p.name}
          </h3>
          {showId ? <span className="font-mono text-[11px] text-fg-muted">{p.id}</span> : null}
        </div>
        <div aria-live="polite" className="space-y-1">
          {done === "signin" ? (
            <StatusLine p={p} source={{ kind: "stored", type: "oauth", problem: null }} detailed />
          ) : done === "key" ? (
            <StatusLine p={p} source={{ kind: "stored", type: "api_key", problem: null }} />
          ) : (
            <StatusLine p={p} detailed />
          )}
          {current && done === null && !signingIn ? <SourceDetails p={p} source={current} /> : null}
        </div>
      </div>
      {body}
      {notes.map((note, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: notes are static for a render
        <p key={i} className="text-fg-muted">
          {note}
        </p>
      ))}
    </section>
  );
}

function SourceDetails({ p, source }: { p: ProviderAuth; source: CredentialSource }) {
  const shadowed = shadowedText(p);
  return (
    <>
      <p className="text-fg-muted">
        {explanation(source)}{" "}
        {source.kind === "models.json" ? (
          <button
            type="button"
            className="text-accent underline pointer-coarse:min-h-11"
            onClick={() => ui.open("settings", "models")}
          >
            Edit models.json
          </button>
        ) : null}
      </p>
      {shadowed ? <p className="text-fg-muted">{shadowed}</p> : null}
      {p.keyLabel === null && p.login === null ? (
        <p className="text-fg-muted">piglet can't sign in to {p.name}.</p>
      ) : null}
    </>
  );
}

function Actions({
  p,
  onKey,
  onRemove,
}: {
  p: ProviderAuth;
  onKey: () => void;
  onRemove: () => void;
}) {
  const current = inUse(p);
  const account = p.login?.account;
  const signIn = (label: string) =>
    p.login ? (
      <Button
        key="signin"
        variant="primary"
        aria-label={
          label === "Sign in again"
            ? `Sign in to ${p.name} again`
            : `Sign in to ${p.name} with ${account}`
        }
        onClick={() => void startLogin(p)}
      >
        {label}
      </Button>
    ) : null;
  const addKey = p.keyLabel ? (
    <Button key="add" aria-label={`Add API key for ${p.name}`} onClick={onKey}>
      Add API key
    </Button>
  ) : null;
  // The first button of the row takes focus when the overview appears.
  const row = (...buttons: (ReactElement | null)[]) => {
    const present = buttons.filter((b): b is ReactElement => b !== null);
    return (
      <div className="flex flex-wrap gap-2 max-sm:flex-col">
        {present.map((button, i) => (
          <span key={String(button.key)} data-autofocus={i === 0 || undefined} className="contents">
            {button}
          </span>
        ))}
      </div>
    );
  };
  const link = (label: string, onClick: () => void) => (
    <button
      type="button"
      className="text-accent underline pointer-coarse:min-h-11"
      onClick={onClick}
    >
      {label}
    </button>
  );

  if (!current) return row(account ? signIn(`Sign in with ${account}`) : null, addKey);
  if (current.kind !== "stored")
    return row(account ? signIn(`Sign in with ${account}`) : null, addKey);

  const removeLabel = current.type === "oauth" ? "Sign out" : "Remove key";
  const remove = (
    <Button
      key="remove"
      variant="danger"
      aria-label={
        current.type === "oauth" ? `Sign out of ${p.name}` : `Remove API key for ${p.name}`
      }
      onClick={onRemove}
    >
      {removeLabel}
    </Button>
  );
  if (current.type === "oauth") {
    if (current.problem) return row(account ? signIn("Sign in again") : null, remove);
    return (
      <div className="space-y-2">
        {row(remove)}
        {p.keyLabel ? link("Use an API key instead", onKey) : null}
      </div>
    );
  }
  const replace = p.keyLabel ? (
    <Button
      key="replace"
      variant={current.problem ? "primary" : "default"}
      aria-label={`Replace API key for ${p.name}`}
      onClick={onKey}
    >
      Replace key
    </Button>
  ) : null;
  if (current.problem) return row(replace, remove);
  return (
    <div className="space-y-2">
      {row(replace, remove)}
      {account ? link(`Sign in with ${account} instead`, () => void startLogin(p)) : null}
    </div>
  );
}

/** Loading and load-failure states shared by the credential surfaces. */
export function ProvidersLoading({ error }: { error: string | null }) {
  if (!error) return <p className="text-[13px] text-fg-muted">Loading providers…</p>;
  return (
    <div className="space-y-2 text-[13px]">
      <p role="alert">Couldn't load providers. {error}</p>
      <Button onClick={() => void refreshProviders()}>Retry</Button>
    </div>
  );
}

/** Shown above a list that is still from an earlier load when reloading it failed. */
export function RefreshFailed({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="mb-3 flex flex-wrap items-center gap-2 text-[13px]">
      <span className="min-w-0 flex-1">Couldn't refresh providers. {error}</span>
      <Button onClick={() => void refreshProviders()}>Retry</Button>
    </div>
  );
}

/** "Some providers may be missing because models.json has a problem: …" */
export function ModelsWarning({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="mb-3 flex gap-2 rounded-md border border-line bg-subtle p-3 text-[13px]">
      <Icon name="alert" size={14} className="mt-0.5 shrink-0 text-warning" />
      <p>
        Some providers may be missing because models.json has a problem: {warnings[0]}{" "}
        <button
          type="button"
          className="text-accent underline pointer-coarse:min-h-11"
          onClick={() => ui.open("settings", "models")}
        >
          Edit models.json
        </button>
      </p>
    </div>
  );
}
