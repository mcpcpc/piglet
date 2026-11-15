import { dismissToast, useApp } from "../state/store";
import { Icon } from "./Icon";

export function Toasts() {
  const toasts = useApp((s) => s.toasts);
  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2"
      aria-live="polite"
    >
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`pointer-events-auto flex items-start gap-2 rounded-md border bg-bg px-3 py-2 text-[13px] shadow-lg ${
            toast.kind === "error" ? "border-danger/50 text-danger" : "border-line"
          }`}
        >
          <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{toast.text}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => dismissToast(toast.id)}
            className="text-fg-faint hover:text-fg"
          >
            <Icon name="close" size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
