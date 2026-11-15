import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
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
          className={`fixed left-1/2 top-[10vh] z-50 flex max-h-[80vh] w-[calc(100vw-2rem)] ${width} -translate-x-1/2 flex-col rounded-lg border border-line bg-bg shadow-xl`}
        >
          <div className="flex items-center border-b border-line px-4 py-3">
            <Dialog.Title className="text-[14px] font-semibold">{title}</Dialog.Title>
            <Dialog.Close
              className="ml-auto rounded p-1 text-fg-muted hover:bg-hover"
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
      className={`rounded-md px-3 py-1.5 text-[13px] disabled:opacity-50 ${styles} ${className}`}
    >
      {children}
    </button>
  );
}

export const inputClass =
  "w-full rounded-md border border-line bg-bg px-2.5 py-1.5 text-[13px] outline-none focus:border-accent";
