// A panel that slides in from the left or right edge over a scrim. On narrow screens the sidebar
// and the Changes/Bash drawer render inside one, so a tap outside, Esc or a close button
// dismisses them, and focus stays inside while they are open.

import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

const SIDES = {
  left: "left-0 data-[state=open]:animate-slide-in-left data-[state=closed]:animate-slide-out-left",
  right:
    "right-0 data-[state=open]:animate-slide-in-right data-[state=closed]:animate-slide-out-right",
};

/** Render `children` as a modal edge panel; `width` is a literal Tailwind width class. */
export function OffCanvas({
  open,
  onClose,
  side,
  title,
  width,
  children,
}: {
  open: boolean;
  onClose: () => void;
  side: "left" | "right";
  title: string;
  width: string;
  children: ReactNode;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out" />
        <Dialog.Content
          aria-describedby={undefined}
          className={`fixed inset-y-0 z-40 flex max-w-full outline-none ${width} ${SIDES[side]}`}
        >
          <Dialog.Title className="sr-only">{title}</Dialog.Title>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
