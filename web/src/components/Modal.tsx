import * as Dialog from "@radix-ui/react-dialog";
import { type ReactNode, useState } from "react";
import { Icon } from "./Icon";

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
  description,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
  description?: string;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30" />
        <Dialog.Content
          className={`fixed left-1/2 top-[10dvh] z-50 flex max-h-[80dvh] w-[calc(100vw-2rem)] ${width} -translate-x-1/2 flex-col rounded-lg border border-line bg-bg shadow-xl max-sm:top-2 max-sm:max-h-[calc(100dvh-1rem)] max-sm:w-[calc(100vw-1rem)]`}
        >
          <div className="flex items-center border-b border-line px-4 py-3">
            <Dialog.Title className="text-[14px] font-semibold">{title}</Dialog.Title>
            <Dialog.Close
              className="ml-auto rounded p-1 text-fg-muted hover:bg-hover pointer-coarse:p-2.5"
              aria-label="Close"
            >
              <Icon name="close" size={14} />
            </Dialog.Close>
          </div>
          {description ? (
            <Dialog.Description className="px-4 pt-3 text-[13px] text-fg-muted">
              {description}
            </Dialog.Description>
          ) : (
            <Dialog.Description className="sr-only">{title}</Dialog.Description>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto p-4">{children}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  type = "button",
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  const styles = {
    default: "border border-line bg-bg hover:bg-hover",
    primary: "bg-accent text-accent-fg hover:opacity-90",
    danger: "border border-danger/40 text-danger hover:bg-del-bg",
    ghost: "hover:bg-hover",
  }[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`whitespace-nowrap rounded-md px-3 py-1.5 text-[13px] disabled:opacity-50 pointer-coarse:py-2.5 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

// Text fields show focus as an accent border plus a soft halo on their own radius. 16px text on
// touch devices stops iOS Safari from zooming in when a field is focused.
export const fieldFocus = "outline-none focus:border-accent focus:ring-2 focus:ring-accent/25";

export const inputClass = `w-full rounded-md border border-line bg-bg px-2.5 py-1.5 text-[13px] pointer-coarse:text-base ${fieldFocus}`;

/**
 * A dialog body that asks for text: one autofocused line, or a monospace editor when
 * `multiline`. Submitting (Enter in the line field) calls `onSubmit`; `onCancel` adds a button.
 */
export function TextForm({
  initial = "",
  placeholder,
  multiline = false,
  submitLabel = "OK",
  onSubmit,
  onCancel,
}: {
  initial?: string;
  placeholder?: string;
  multiline?: boolean;
  submitLabel?: string;
  onSubmit: (text: string) => void;
  onCancel?: () => void;
}) {
  const [text, setText] = useState(initial);
  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit(text);
      }}
    >
      {multiline ? (
        <textarea
          className={`${inputClass} h-48 font-mono`}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
      ) : (
        <input
          className={inputClass}
          placeholder={placeholder}
          value={text}
          onChange={(e) => setText(e.target.value)}
          // biome-ignore lint/a11y/noAutofocus: the dialog exists to type into
          autoFocus
        />
      )}
      <div className="flex gap-2">
        <Button type="submit" variant="primary">
          {submitLabel}
        </Button>
        {onCancel ? <Button onClick={onCancel}>Cancel</Button> : null}
      </div>
    </form>
  );
}
