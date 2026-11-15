// /login: OAuth sign-in for providers whose flows piglet supports. The browser opens the
// provider page; if its redirect cannot reach piglet, the user pastes the final URL here.

import { useEffect, useState } from "react";
import { openExternal } from "../lib/native";
import { cancelLogin, reportError, request, startLogin, ui, useApp } from "../state/store";
import { Spinner } from "./Icon";
import { Button, inputClass, Modal } from "./Modal";

export function LoginDialog() {
  const open = useApp((s) => s.dialog === "login");
  const login = useApp((s) => s.login);
  const [providers, setProviders] = useState<{ id: string; name: string }[]>([]);
  const [pasted, setPasted] = useState("");

  useEffect(() => {
    if (open)
      request<{ id: string; name: string }[]>("login_providers", {})
        .then(setProviders)
        .catch(reportError);
  }, [open]);

  const close = () => {
    void cancelLogin().catch(reportError);
    setPasted("");
    ui.close();
  };

  return (
    <Modal open={open} onClose={close} title="Sign in to a provider" width="max-w-md">
      {!login ? (
        <div className="space-y-2">
          {providers.map((provider) => (
            <Button
              key={provider.id}
              className="w-full text-left"
              onClick={() => void startLogin(provider.id).catch(reportError)}
            >
              {provider.name}
            </Button>
          ))}
          <p className="pt-2 text-[12px] text-fg-muted">
            Other providers use API keys — add them under Settings → Providers. Claude subscriptions
            cannot be used from third-party apps; use an Anthropic API key instead.
          </p>
        </div>
      ) : login.status === "waiting" ? (
        <div className="space-y-3 text-[13px]">
          <p className="flex items-center gap-2">
            <Spinner /> {login.message ?? `Starting ${login.provider} sign-in…`}
          </p>
          {login.url ? (
            <button
              type="button"
              className="text-accent underline"
              onClick={() => login.url && void openExternal(login.url)}
            >
              Open the sign-in page again
            </button>
          ) : null}
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              request("login_respond", { loginId: login.loginId, value: pasted }).catch(
                reportError,
              );
            }}
          >
            <input
              className={inputClass}
              placeholder="Paste the redirect URL or code"
              value={pasted}
              onChange={(e) => setPasted(e.target.value)}
            />
            <Button type="submit" disabled={!pasted}>
              Submit
            </Button>
          </form>
        </div>
      ) : (
        <div className="space-y-3 text-[13px]">
          <p className={login.status === "error" ? "text-danger" : ""}>
            {login.status === "success"
              ? `Signed in to ${login.provider}.`
              : login.status === "cancelled"
                ? "Sign-in cancelled."
                : `Sign-in failed: ${login.message}`}
          </p>
          <Button variant="primary" onClick={close}>
            Done
          </Button>
        </div>
      )}
    </Modal>
  );
}
