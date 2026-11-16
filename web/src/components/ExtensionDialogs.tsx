// Extension UI requests (select / confirm / input / editor), answered with extension_ui_response.

import {
  type ExtensionRequest,
  reportError,
  request,
  resolveExtensionRequest,
  useApp,
} from "../state/store";
import { Button, Modal, TextForm } from "./Modal";

function respond(
  req: ExtensionRequest,
  reply: { value?: unknown; confirmed?: boolean; cancelled?: boolean },
) {
  resolveExtensionRequest(req.id);
  request("extension_ui_response", {
    sessionId: req.sessionId,
    requestId: req.id,
    value: reply.value ?? null,
    confirmed: reply.confirmed ?? null,
    cancelled: reply.cancelled ?? false,
  }).catch(reportError);
}

function RequestBody({ req }: { req: ExtensionRequest }) {
  const params = req.params;
  if (req.method === "select") {
    const options = Array.isArray(params.options) ? params.options.map(String) : [];
    return (
      <div className="flex flex-col gap-1">
        {options.map((option) => (
          <Button
            key={option}
            className="text-left"
            onClick={() => respond(req, { value: option })}
          >
            {option}
          </Button>
        ))}
      </div>
    );
  }
  if (req.method === "confirm") {
    return (
      <div className="space-y-3">
        {params.message ? (
          <p className="whitespace-pre-wrap text-[13px]">{String(params.message)}</p>
        ) : null}
        <div className="flex gap-2">
          <Button variant="primary" onClick={() => respond(req, { confirmed: true })}>
            Yes
          </Button>
          <Button onClick={() => respond(req, { confirmed: false })}>No</Button>
        </div>
      </div>
    );
  }
  return (
    <TextForm
      initial={String(params.prefill ?? "")}
      placeholder={String(params.placeholder ?? "")}
      multiline={req.method === "editor"}
      onSubmit={(value) => respond(req, { value })}
    />
  );
}

export function ExtensionDialogs() {
  const req = useApp((s) => s.extensionRequests[0]);
  if (!req) return null;
  return (
    <Modal
      open
      onClose={() => respond(req, { cancelled: true })}
      title={String(req.params.title ?? "Extension")}
    >
      <RequestBody key={req.id} req={req} />
    </Modal>
  );
}

/** Status-line segments and composer widgets set by extensions. */
export function ExtensionSurfaces({
  sessionId,
  place,
}: {
  sessionId: string;
  place: "status" | "widget";
}) {
  const status = useApp((s) => s.extensionStatus[sessionId]);
  const widgets = useApp((s) => s.extensionWidgets[sessionId]);
  if (place === "status") {
    return status ? (
      <span className="flex min-w-0 gap-3">
        {Object.entries(status).map(([key, text]) => (
          <span key={key} className="truncate">
            {text}
          </span>
        ))}
      </span>
    ) : null;
  }
  if (!widgets) return null;
  return (
    <div className="mx-auto mb-2 w-full max-w-3xl space-y-1 px-4">
      {Object.entries(widgets).map(([key, lines]) => (
        <pre
          key={key}
          className="whitespace-pre-wrap rounded-md border border-line bg-subtle px-3 py-1.5 font-mono text-[12px] text-fg-muted"
        >
          {lines.join("\n")}
        </pre>
      ))}
    </div>
  );
}
