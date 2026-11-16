import {
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { applyCompletion, type CompletionToken, matchCommand, tokenAt } from "../lib/completion";
import { COARSE_POINTER, useMediaQuery } from "../lib/media";
import type { ImageContent } from "../protocol/commands";
import { abort, BUILTIN_COMMANDS, cycleThinking, restoreQueue, send } from "../state/actions";
import type { SessionView } from "../state/sessionView";
import { reportError, request, setDraft, useApp } from "../state/store";
import { Icon } from "./Icon";
import { ModelChip, ThinkingChip } from "./Pickers";

interface MenuItem {
  value: string;
  label: string;
  detail: string;
}

async function readImages(files: Iterable<File>): Promise<ImageContent[]> {
  const images = [...files].filter((file) => file.type.startsWith("image/"));
  return Promise.all(
    images.map(
      (file) =>
        new Promise<ImageContent>((resolve, reject) => {
          const reader = new FileReader();
          reader.onload = () => {
            const url = String(reader.result);
            resolve({ type: "image", mimeType: file.type, data: url.slice(url.indexOf(",") + 1) });
          };
          reader.onerror = () => reject(reader.error);
          reader.readAsDataURL(file);
        }),
    ),
  );
}

function QueueBar({ view }: { view: SessionView }) {
  const { steering, followUp } = view.state.queue;
  if (!steering.length && !followUp.length) return null;
  const rows = [
    ...steering.map((text) => ({ text, kind: "steer" })),
    ...followUp.map((text) => ({ text, kind: "follow-up" })),
  ];
  return (
    <div className="mb-2 space-y-1">
      {rows.map((row, index) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: queue entries have no id
          key={index}
          className="flex items-center gap-2 rounded-md bg-subtle px-2 py-1 text-[12px] text-fg-muted"
        >
          <span className="text-fg-faint">↳ queued ({row.kind})</span>
          <span className="min-w-0 flex-1 truncate">{row.text}</span>
        </div>
      ))}
      <button
        type="button"
        onClick={() => restoreQueue().catch(reportError)}
        className="text-[11px] text-fg-faint hover:text-fg"
      >
        Edit queued messages (Alt+↑)
      </button>
    </div>
  );
}

export function Composer({ view }: { view: SessionView }) {
  const draft = useApp((s) => s.drafts[view.id] ?? "");
  const commands = useApp((s) => s.commands[view.id]);
  const [images, setImages] = useState<ImageContent[]>([]);
  const [token, setToken] = useState<CompletionToken | null>(null);
  const [files, setFiles] = useState<string[]>([]);
  const [selected, setSelected] = useState(0);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const streaming = view.state.isStreaming;
  // Soft keyboards have no Shift+Enter, so on touch devices Return inserts a newline and the
  // buttons send. A hardware keyboard can still send with ⌘/Ctrl+Enter (Alt+Enter queues).
  const touch = useMediaQuery(COARSE_POINTER);
  const canSend = Boolean(draft.trim() || images.length);

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 320)}px`;
  });

  // biome-ignore lint/correctness/useExhaustiveDependencies: refocus when switching sessions
  useEffect(() => {
    textarea.current?.focus();
  }, [view.id]);

  useEffect(() => {
    if (token?.kind !== "file") return;
    const handle = window.setTimeout(() => {
      request<string[]>("list_files", { sessionId: view.id, query: token.query, limit: 30 })
        .then(setFiles)
        .catch(() => setFiles([]));
    }, 80);
    return () => window.clearTimeout(handle);
  }, [token, view.id]);

  const items: MenuItem[] = useMemo(() => {
    if (!token) return [];
    if (token.kind === "file") return files.map((f) => ({ value: f, label: f, detail: "" }));
    const all: MenuItem[] = [
      ...BUILTIN_COMMANDS.map((c) => ({
        value: c.name,
        label: `/${c.name}`,
        detail: c.description,
      })),
      ...(commands ?? []).map((c) => ({
        value: c.name,
        label: `/${c.name}`,
        detail: c.description,
      })),
    ];
    return all
      .map((item) => ({ item, score: matchCommand(item.value, token.query) }))
      .filter((x) => x.score !== null)
      .sort((a, b) => (a.score ?? 0) - (b.score ?? 0))
      .map((x) => x.item)
      .slice(0, 12);
  }, [token, files, commands]);

  const updateToken = (text: string, cursor: number) => {
    setToken(tokenAt(text, cursor));
    setSelected(0);
  };

  const complete = (item: MenuItem) => {
    if (!token) return;
    const result = applyCompletion(draft, token, item.value);
    setDraft(view.id, result.text);
    setToken(null);
    requestAnimationFrame(() => {
      textarea.current?.setSelectionRange(result.cursor, result.cursor);
      textarea.current?.focus();
    });
  };

  const submit = async (mode: "auto" | "followUp") => {
    const sent = await send(draft, images, mode);
    if (sent) {
      setDraft(view.id, "");
      setImages([]);
      setToken(null);
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    // Keys that commit or cancel an IME composition (Safari reports keyCode 229) belong to it.
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (items.length && token) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        const delta = event.key === "ArrowDown" ? 1 : -1;
        setSelected((s) => (s + delta + items.length) % items.length);
        return;
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey && !event.altKey)) {
        const item = items[selected];
        if (
          item &&
          !(event.key === "Enter" && token.kind === "command" && `/${item.value}` === draft.trim())
        ) {
          event.preventDefault();
          complete(item);
          return;
        }
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setToken(null);
        return;
      }
    }
    const sendKey =
      event.key === "Enter" &&
      !event.shiftKey &&
      (!touch || event.metaKey || event.ctrlKey || event.altKey);
    if (sendKey) {
      event.preventDefault();
      void submit(event.altKey ? "followUp" : "auto");
    } else if (event.key === "Escape" && (streaming || view.bash)) {
      event.preventDefault();
      void abort().catch(reportError);
    } else if (event.key === "Tab" && event.shiftKey) {
      event.preventDefault();
      void cycleThinking().catch(reportError);
    } else if (event.key === "ArrowUp" && event.altKey) {
      event.preventDefault();
      void restoreQueue().catch(reportError);
    }
  };

  const onPaste = async (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = await readImages(event.clipboardData.files);
    if (pasted.length) {
      event.preventDefault();
      setImages((current) => [...current, ...pasted]);
    }
  };

  const onDrop = async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const dropped = await readImages(event.dataTransfer.files);
    setImages((current) => [...current, ...dropped]);
  };

  const placeholder = touch
    ? streaming
      ? "Steer the agent, or queue a follow-up…"
      : "Message · / commands · @ files · ! shell"
    : streaming
      ? "Steer the agent (Enter) or queue a follow-up (Alt+Enter)…"
      : "Message · / for commands · @ for files · ! to run a shell command";

  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-3">
      <QueueBar view={view} />
      <div
        className="relative rounded-lg border border-line bg-bg shadow-sm has-[textarea:focus]:border-accent has-[textarea:focus]:ring-2 has-[textarea:focus]:ring-accent/25"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => void onDrop(e)}
      >
        {token && items.length ? (
          <div
            className="absolute bottom-full left-0 z-20 mb-1 max-h-72 w-full overflow-y-auto rounded-lg border border-line bg-bg p-1 shadow-xl"
            role="listbox"
          >
            {items.map((item, index) => (
              <button
                type="button"
                role="option"
                aria-selected={index === selected}
                key={`${item.label}:${item.detail}`}
                onMouseDown={(e) => {
                  e.preventDefault();
                  complete(item);
                }}
                className={`flex w-full items-baseline gap-3 rounded px-2 py-1 text-left text-[13px] pointer-coarse:py-2 ${index === selected ? "bg-hover" : ""}`}
              >
                <span className="shrink-0 font-mono">{item.label}</span>
                <span className="truncate text-[12px] text-fg-faint">{item.detail}</span>
              </button>
            ))}
          </div>
        ) : null}
        {images.length ? (
          <div className="flex flex-wrap gap-2 px-3 pt-3">
            {images.map((image, index) => (
              <div key={image.data.slice(-24)} className="relative">
                <img
                  alt="Attachment"
                  className="h-14 rounded border border-line"
                  src={`data:${image.mimeType};base64,${image.data}`}
                />
                <button
                  type="button"
                  aria-label="Remove image"
                  onClick={() => setImages((current) => current.filter((_, i) => i !== index))}
                  className="absolute -right-1.5 -top-1.5 rounded-full border border-line bg-bg p-0.5 after:absolute after:-inset-2"
                >
                  <Icon name="close" size={10} />
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <textarea
          ref={textarea}
          value={draft}
          rows={1}
          placeholder={placeholder}
          aria-label="Message"
          onChange={(e) => {
            setDraft(view.id, e.target.value);
            updateToken(e.target.value, e.target.selectionStart);
          }}
          onClick={(e) => updateToken(e.currentTarget.value, e.currentTarget.selectionStart)}
          onKeyDown={onKeyDown}
          onPaste={(e) => void onPaste(e)}
          className="block max-h-80 w-full resize-none pointer-coarse:max-h-[40dvh] bg-transparent px-3 pb-1 pt-3 text-[14px] leading-relaxed outline-none placeholder:text-fg-faint pointer-coarse:text-base"
        />
        <div className="flex items-center gap-1 px-2 pb-2">
          <label
            className="cursor-pointer rounded-md p-1 text-fg-muted hover:bg-hover hover:text-fg pointer-coarse:p-2.5"
            title="Attach images"
          >
            <Icon name="image" size={15} />
            <input
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={(e) => {
                const input = e.currentTarget;
                readImages(input.files ?? [])
                  .then((added) => setImages((current) => [...current, ...added]))
                  .catch(reportError);
                input.value = "";
              }}
            />
          </label>
          <ModelChip sessionId={view.id} model={view.state.model} />
          {view.state.model?.reasoning ? (
            <ThinkingChip sessionId={view.id} level={view.state.thinkingLevel} />
          ) : null}
          <div className="ml-auto flex shrink-0 items-center gap-1">
            {streaming && canSend ? (
              <button
                type="button"
                onClick={() => void submit("followUp")}
                className="rounded-md px-2 py-1 text-[12px] text-fg-muted hover:bg-hover hover:text-fg pointer-coarse:py-2"
                title="Queue a follow-up for when the agent finishes (Alt+Enter)"
              >
                Queue
              </button>
            ) : null}
            {streaming && canSend ? (
              <button
                type="button"
                onClick={() => void submit("auto")}
                className="rounded-md bg-accent p-1.5 text-accent-fg hover:opacity-90 pointer-coarse:p-2.5"
                aria-label="Steer (Enter)"
                title="Steer the agent now (Enter)"
              >
                <Icon name="send" size={14} />
              </button>
            ) : null}
            {streaming || view.bash ? (
              <button
                type="button"
                onClick={() => abort().catch(reportError)}
                className="rounded-md bg-fg p-1.5 text-bg hover:opacity-80 pointer-coarse:p-2.5"
                aria-label="Stop (Esc)"
                title="Stop (Esc)"
              >
                <Icon name="stop" size={14} />
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void submit("auto")}
                disabled={!canSend}
                className="rounded-md bg-accent p-1.5 text-accent-fg hover:opacity-90 disabled:opacity-40 pointer-coarse:p-2.5"
                aria-label="Send (Enter)"
                title="Send (Enter)"
              >
                <Icon name="send" size={14} />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
