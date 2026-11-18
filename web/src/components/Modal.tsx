import * as Dialog from "@radix-ui/react-dialog";
import { type ButtonHTMLAttributes, type ReactNode, type RefObject, useState } from "react";
import { Icon } from "./Icon";

export function Modal({
  open,
  onClose,
  title,
  children,
  width = "max-w-lg",
  description,
  initialFocus,
  onEscapeKeyDown,
  className = "",
  bodyClassName = "overflow-y-auto p-4",
  status,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  width?: string;
  description?: string;
  /** Element to focus on open instead of the first focusable one (the close button). */
  initialFocus?: RefObject<HTMLElement | null>;
  /** Runs before Esc closes the dialog; `event.preventDefault()` keeps it open. */
  onEscapeKeyDown?: (event: KeyboardEvent) => void;
  /** Extra classes for the dialog box, such as a fixed height. */
  className?: string;
  /** Classes for the body under the title bar; it scrolls by default. */
  bodyClassName?: string;
  /** Live status in the title bar, between the title and the close button (e.g. "Saved"). */
  status?: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-x-0 bottom-0 top-(--titlebar-bottom) z-40 bg-scrim" />
        <Dialog.Content
          onEscapeKeyDown={onEscapeKeyDown}
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={(event) => {
            if (!initialFocus?.current) return;
            event.preventDefault();
            initialFocus.current.focus();
          }}
          className={`fixed left-1/2 top-[10dvh] z-50 flex max-h-[80dvh] w-[calc(100vw-2rem)] ${width} -translate-x-1/2 flex-col rounded-lg border border-line bg-bg shadow-float max-sm:top-[calc(var(--titlebar-bottom)+0.5rem)] max-sm:max-h-[calc(100dvh-1rem-var(--titlebar-bottom))] max-sm:w-[calc(100vw-1rem)] ${className}`}
        >
          <div className="flex items-center border-b border-line px-4 py-3">
            <Dialog.Title className="text-[14px] font-semibold">{title}</Dialog.Title>
            {status ? <div className="ml-auto mr-2 flex items-center">{status}</div> : null}
            <Dialog.Close
              className={`-my-1 flex items-center justify-center rounded p-1 text-fg-muted hover:bg-hover pointer-coarse:-my-2.5 pointer-coarse:size-11 ${status ? "" : "ml-auto"}`}
              aria-label="Close"
            >
              <Icon name="close" size={14} />
            </Dialog.Close>
          </div>
          {description ? (
            <Dialog.Description className="px-4 pt-3 text-[13px] text-fg-muted">
              {description}
            </Dialog.Description>
          ) : null}
          <div data-modal-body className={`min-h-0 flex-1 ${bodyClassName}`}>
            {children}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/**
 * `default` is the secondary button. `danger` starts a destructive action, and `dangerFill`
 * commits one (the confirm's button). Disabled buttons keep readable text instead of fading.
 */
type ButtonVariant = "default" | "primary" | "danger" | "dangerFill" | "ghost";

/** Button styling, also used for links that look like buttons. */
export function buttonClass(variant: ButtonVariant = "default", className = ""): string {
  const styles = {
    default: "bg-muted text-fg hover:bg-hover disabled:hover:bg-muted",
    primary:
      "bg-accent text-accent-fg hover:opacity-90 disabled:bg-muted disabled:hover:opacity-100",
    danger: "bg-muted text-danger hover:bg-del-bg disabled:hover:bg-muted",
    dangerFill:
      "bg-danger text-accent-fg hover:opacity-90 disabled:bg-muted disabled:hover:opacity-100",
    ghost: "text-fg hover:bg-hover disabled:hover:bg-transparent",
  }[variant];
  return `inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-transparent px-3 py-1.5 text-[13px] disabled:cursor-not-allowed disabled:text-fg-muted pointer-coarse:py-2.5 ${styles} ${className}`;
}

export function Button({
  children,
  variant = "default",
  type = "button",
  className = "",
  ...rest
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  type?: "button" | "submit";
  className?: string;
} & Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "className" | "children">) {
  return (
    <button type={type} {...rest} className={buttonClass(variant, className)}>
      {children}
    </button>
  );
}

// Text fields show focus as an accent border plus a soft halo on their own radius. 16px text on
// touch devices stops iOS Safari from zooming in when a field is focused.
export const fieldFocus =
  "outline-none focus:border-accent focus:ring-[3px] focus:ring-accent/30 aria-invalid:border-danger aria-invalid:ring-[3px] aria-invalid:ring-del-bg";

/** A filled field without a width: no outline until it has focus or an error. */
export const fieldClass = `rounded-md border border-transparent bg-muted px-2.5 py-1.5 text-[13px] text-fg placeholder:text-fg-muted disabled:cursor-not-allowed disabled:text-fg-muted pointer-coarse:text-base ${fieldFocus}`;

/** A filled field as wide as its container. */
export const inputClass = `w-full ${fieldClass}`;

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
