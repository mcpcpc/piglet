// /login and /logout. /login follows Pi: choose how to connect, then the provider, then the same
// provider page Settings → Providers uses. /logout lists only credentials saved in piglet.

import { type KeyboardEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { COARSE_POINTER, matches as mediaMatches } from "../lib/media";
import {
  configured,
  findProvider,
  inUse,
  methodTag,
  savedAndWorking,
  statusText,
} from "../lib/providers";
import type { ProviderAuth } from "../protocol/types";
import { refreshProviders, startLogin, ui, useApp } from "../state/store";
import {
  ConfirmRemove,
  type EscapeRef,
  ModelsWarning,
  ProviderList,
  ProviderPage,
  ProvidersLoading,
  RefreshFailed,
  StatusLine,
  TOUCH_TARGETS,
} from "./Credentials";
import { Icon } from "./Icon";
import { Button, Modal } from "./Modal";

type Step =
  | { kind: "methods" }
  | { kind: "keys" }
  | { kind: "page"; id: string; from: "methods" | "keys"; startWithKey: boolean; direct: boolean };

const optionClass =
  "flex w-full items-center gap-3 px-3 py-2 text-left text-[13px] outline-none hover:bg-hover focus-visible:bg-hover pointer-coarse:min-h-11";

/** Move focus between a group's buttons with the arrow keys. */
function rovingFocus(event: KeyboardEvent<HTMLElement>): void {
  const keys: Record<string, number> = { ArrowDown: 1, ArrowUp: -1 };
  const step = keys[event.key];
  if (!step) return;
  const buttons = [...event.currentTarget.querySelectorAll<HTMLButtonElement>("button")];
  const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
  const next = buttons[Math.min(buttons.length - 1, Math.max(0, index + step))];
  if (next) {
    event.preventDefault();
    next.focus();
  }
}

function signInLine(p: ProviderAuth): ReactNode {
  const label = `${p.name} ${methodTag(p)}`;
  if (!configured(p)) return label;
  return (
    <>
      {label} · <StatusLine p={p} neutral={!savedAndWorking(p, "oauth")} />
    </>
  );
}

function keyLine(p: ProviderAuth): ReactNode {
  const current = inUse(p);
  if (current?.kind === "stored" && current.type === "oauth") return <StatusLine p={p} neutral />;
  return <StatusLine p={p} />;
}

function Methods({
  providers,
  onSignIn,
  onKeys,
}: {
  providers: ProviderAuth[];
  onSignIn: (p: ProviderAuth) => void;
  onKeys: () => void;
}) {
  const headingId = useId();
  const group = useRef<HTMLFieldSetElement>(null);
  useEffect(() => group.current?.querySelector("button")?.focus(), []);
  const logins = providers.filter((p) => p.login);
  const keyCount = providers.filter((p) => p.keyLabel).length;
  return (
    <div className="space-y-3 text-[13px]">
      <p id={headingId} className="font-medium">
        How do you want to connect?
      </p>
      <fieldset
        ref={group}
        aria-labelledby={headingId}
        onKeyDown={rovingFocus}
        className="divide-y divide-line overflow-hidden rounded-md border border-line"
      >
        {logins.map((p) => (
          <button key={p.id} type="button" className={optionClass} onClick={() => onSignIn(p)}>
            <span className="min-w-0 flex-1">
              <span className="block font-medium">Sign in with {p.login?.account}</span>
              <span className="block text-[12px] text-fg-muted">{signInLine(p)}</span>
            </span>
            <Icon name="chevronRight" size={14} className="shrink-0 text-fg-faint" />
          </button>
        ))}
        <button type="button" className={optionClass} onClick={onKeys}>
          <span className="min-w-0 flex-1">
            <span className="block font-medium">Use an API key</span>
            <span className="block text-[12px] text-fg-muted">For any of {keyCount} providers</span>
          </span>
          <Icon name="chevronRight" size={14} className="shrink-0 text-fg-faint" />
        </button>
      </fieldset>
      <p className="text-fg-muted">
        Claude subscriptions can't be used in third-party apps. For Anthropic, use an API key.
      </p>
      <Button
        variant="ghost"
        className="-ml-3 text-accent pointer-coarse:min-h-11"
        onClick={() => ui.open("settings", "providers")}
      >
        Manage all providers in Settings
      </Button>
    </div>
  );
}

export function LoginDialog() {
  const open = useApp((s) => s.dialog === "login");
  const status = useApp((s) => s.providers);
  const error = useApp((s) => s.providersError);
  const target = useApp((s) => s.credentialTarget);
  const [step, setStep] = useState<Step>({ kind: "methods" });
  const [selected, setSelected] = useState("");
  const pageEscape: EscapeRef = useRef(null);
  const keysHeading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (!open) return;
    setStep({ kind: "methods" });
    void refreshProviders();
  }, [open]);

  // `/login <text>` opens the one provider the text names.
  useEffect(() => {
    if (!open || !target || !status) return;
    useApp.setState({ credentialTarget: null });
    const found = findProvider(status.providers, target);
    if (found)
      setStep({ kind: "page", id: found.id, from: "methods", startWithKey: false, direct: false });
  }, [open, target, status]);

  useEffect(() => {
    if (step.kind === "keys" && mediaMatches(COARSE_POINTER)) keysHeading.current?.focus();
  }, [step.kind]);

  const signIn = (p: ProviderAuth) => {
    const direct = !savedAndWorking(p, "oauth");
    if (direct) void startLogin(p);
    setStep({ kind: "page", id: p.id, from: "methods", startWithKey: false, direct });
  };
  const back = () => setStep({ kind: step.kind === "page" ? step.from : "methods" });
  const provider =
    step.kind === "page" ? status?.providers.find((p) => p.id === step.id) : undefined;

  let body: ReactNode;
  if (!status) body = <ProvidersLoading error={error} />;
  else if (step.kind === "page" && provider)
    body = (
      <ProviderPage
        key={provider.id}
        p={provider}
        context="login"
        backLabel={step.from === "keys" ? "Back to providers" : "Back"}
        onBack={back}
        onDone={ui.close}
        onSignInExit={step.direct ? back : undefined}
        startWithKey={step.startWithKey}
        escapeRef={pageEscape}
      />
    );
  else if (step.kind === "keys")
    body = (
      <div className="space-y-3 text-[13px]">
        <Button
          variant="ghost"
          aria-label="Back"
          onClick={back}
          className="-ml-2 inline-flex items-center gap-1 text-accent pointer-coarse:min-h-11"
        >
          <Icon name="chevronLeft" size={14} /> Back
        </Button>
        <h3 ref={keysHeading} tabIndex={-1} className="text-[15px] font-semibold outline-none">
          Use an API key
        </h3>
        <ModelsWarning warnings={status.warnings} />
        <ProviderList
          providers={status.providers.filter((p) => p.keyLabel)}
          selected={selected}
          onSelected={setSelected}
          line2={keyLine}
          autoFocus={!mediaMatches(COARSE_POINTER)}
          onOpen={(p) =>
            setStep({
              kind: "page",
              id: p.id,
              from: "keys",
              startWithKey: !savedAndWorking(p, "api_key"),
              direct: false,
            })
          }
        />
      </div>
    );
  else
    body = (
      <Methods
        providers={status.providers}
        onSignIn={signIn}
        onKeys={() => setStep({ kind: "keys" })}
      />
    );

  return (
    <Modal
      open={open}
      onClose={ui.close}
      title="Sign in"
      width="max-w-md"
      onEscapeKeyDown={(event) => {
        if (step.kind === "methods") return;
        event.preventDefault();
        if (step.kind === "page" && pageEscape.current?.()) return;
        back();
      }}
    >
      <div className={TOUCH_TARGETS}>
        {status ? <RefreshFailed error={error} /> : null}
        {body}
      </div>
    </Modal>
  );
}

function logoutLine(p: ProviderAuth): ReactNode {
  const current = inUse(p);
  const kind =
    current?.kind === "stored" && current.type === "oauth"
      ? p.login
        ? `${p.login.account} ${methodTag(p)}`
        : "Sign-in"
      : "API key";
  // A working credential needs no status here; a problem is worth showing before removing it.
  if (statusText(p).tone !== "warn") return kind;
  return (
    <>
      {kind} · <StatusLine p={p} />
    </>
  );
}

export function LogoutDialog() {
  const open = useApp((s) => s.dialog === "logout");
  const status = useApp((s) => s.providers);
  const error = useApp((s) => s.providersError);
  const target = useApp((s) => s.credentialTarget);
  const [confirming, setConfirming] = useState<string | null>(null);
  const list = useRef<HTMLUListElement>(null);
  const closeRef = useRef<HTMLDivElement>(null);
  const saved = status?.providers.filter((p) => inUse(p)?.kind === "stored") ?? [];

  useEffect(() => {
    if (!open) return;
    setConfirming(null);
    void refreshProviders();
  }, [open]);

  // `/logout <text>` opens the confirmation for the one saved credential the text names.
  useEffect(() => {
    if (!open || !target || !status) return;
    useApp.setState({ credentialTarget: null });
    const found = findProvider(
      status.providers.filter((p) => inUse(p)?.kind === "stored"),
      target,
    );
    if (found) setConfirming(found.id);
  }, [open, target, status]);

  const loaded = status !== null;
  useEffect(() => {
    if (open && loaded)
      requestAnimationFrame(() =>
        (list.current ?? closeRef.current)?.querySelector<HTMLElement>("button")?.focus(),
      );
  }, [open, loaded]);

  const removed = (index: number) => {
    setConfirming(null);
    requestAnimationFrame(() => {
      const buttons = list.current?.querySelectorAll<HTMLButtonElement>("li > div button");
      const next = buttons?.[Math.min(index, (buttons?.length ?? 1) - 1)];
      if (next) next.focus();
      else closeRef.current?.querySelector("button")?.focus();
    });
  };

  let body: ReactNode;
  if (!status) body = <ProvidersLoading error={error} />;
  else if (saved.length === 0)
    body = (
      <div ref={closeRef} className="space-y-3 text-[13px]">
        <p className="font-medium">Nothing to sign out of.</p>
        <p className="text-fg-muted">
          Only sign-ins and API keys saved in piglet can be removed here. Environment variables and
          models.json aren't changed.
        </p>
        <Button className="max-sm:w-full" onClick={ui.close}>
          Close
        </Button>
      </div>
    );
  else
    body = (
      <div className="space-y-3 text-[13px]">
        <p className="text-fg-muted">
          Remove sign-ins and API keys saved in piglet. Environment variables and models.json aren't
          changed.
        </p>
        <ul ref={list} className="divide-y divide-line rounded-md border border-line">
          {saved.map((p, index) => {
            const current = inUse(p);
            const oauth = current?.kind === "stored" && current.type === "oauth";
            return (
              <li key={p.id} className="space-y-2 px-3 py-2">
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                  <span className="min-w-0 flex-1">
                    <span className="block font-medium [overflow-wrap:anywhere]">{p.name}</span>
                    <span className="block text-[12px] text-fg-muted">{logoutLine(p)}</span>
                  </span>
                  <Button
                    variant="danger"
                    className="max-sm:w-full pointer-coarse:min-h-11"
                    aria-label={oauth ? `Sign out of ${p.name}` : `Remove API key for ${p.name}`}
                    aria-expanded={confirming === p.id}
                    onClick={() => setConfirming(confirming === p.id ? null : p.id)}
                  >
                    {oauth ? "Sign out" : "Remove key"}
                  </Button>
                </div>
                {confirming === p.id ? (
                  <ConfirmRemove
                    p={p}
                    onCancel={() => setConfirming(null)}
                    onDone={() => removed(index)}
                  />
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>
    );

  return (
    <Modal
      open={open}
      onClose={ui.close}
      title="Sign out"
      width="max-w-md"
      onEscapeKeyDown={(event) => {
        if (!confirming) return;
        event.preventDefault();
        setConfirming(null);
      }}
    >
      <div className={TOUCH_TARGETS}>
        {status ? <RefreshFailed error={error} /> : null}
        {body}
      </div>
    </Modal>
  );
}
