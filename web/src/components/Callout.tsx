// A filled notice with a tinted icon, readable body text and optional actions. Filled rather
// than outlined so it can sit inside a dialog without adding a nested border.

import type { ReactNode } from "react";
import { Icon } from "./Icon";

const TONES = {
  danger: { box: "bg-del-bg", icon: "text-danger" },
  warning: { box: "bg-warning/15", icon: "text-warning" },
} as const;

/**
 * A danger or warning notice: an optional title, a body and an optional row of buttons. Pass
 * `role="alert"` only for a notice that appears in response to something happening now.
 */
export function Callout({
  tone,
  title,
  children,
  actions,
  role,
  className = "",
}: {
  tone: keyof typeof TONES;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  role?: "alert" | "status";
  className?: string;
}) {
  const colors = TONES[tone];
  return (
    <div role={role} className={`flex gap-2 rounded-md p-3 text-[13px] ${colors.box} ${className}`}>
      <Icon name="alert" size={14} className={`mt-0.5 shrink-0 ${colors.icon}`} />
      <div className="min-w-0 flex-1 space-y-2">
        {title ? <p className="font-medium">{title}</p> : null}
        {children}
        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
