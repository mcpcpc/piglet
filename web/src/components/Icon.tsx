// A small inline icon set (stroke icons, 16px grid) so the UI needs no icon library.

const PATHS: Record<string, string> = {
  plus: "M8 3v10M3 8h10",
  chevronRight: "M6 4l4 4-4 4",
  chevronDown: "M4 6l4 4 4-4",
  chevronLeft: "M10 4L6 8l4 4",
  close: "M4 4l8 8M12 4l-8 8",
  stop: "M4.5 4.5h7v7h-7z",
  send: "M8 13V3M3.5 7.5L8 3l4.5 4.5",
  branch: "M5 3v10M5 9c0-2 6-1 6-4V3M11 3a1 1 0 1 0 0 .01M5 13a1 1 0 1 0 0 .01",
  panel: "M2.5 3.5h11v9h-11zM10 3.5v9",
  gear: "M6.65 3.29L6.85 1.5A6.6 6.6 0 0 1 9.15 1.5L9.35 3.29A4.9 4.9 0 0 1 11.4 4.48L13.06 3.76A6.6 6.6 0 0 1 14.2 5.74L12.75 6.81A4.9 4.9 0 0 1 12.75 9.19L14.2 10.26A6.6 6.6 0 0 1 13.06 12.24L11.4 11.52A4.9 4.9 0 0 1 9.35 12.71L9.15 14.5A6.6 6.6 0 0 1 6.85 14.5L6.65 12.71A4.9 4.9 0 0 1 4.6 11.52L2.94 12.24A6.6 6.6 0 0 1 1.8 10.26L3.25 9.19A4.9 4.9 0 0 1 3.25 6.81L1.8 5.74A6.6 6.6 0 0 1 2.94 3.76L4.6 4.48A4.9 4.9 0 0 1 6.65 3.29ZM6 8a2 2 0 1 0 4 0a2 2 0 1 0-4 0",
  folder: "M2 4.5h4l1.5 1.5H14v6.5H2z",
  edit: "M10.5 3l2.5 2.5L6 12.5H3.5V10z",
  copy: "M5.5 5.5h7v7h-7zM3.5 10.5v-7h7",
  check: "M3.5 8.5l3 3 6-7",
  alert: "M8 3l6 10H2zM8 7v3M8 11.5v.01",
  image: "M2.5 3.5h11v9h-11zM5.5 6.5a1 1 0 1 0 0 .01M13.5 10.5l-3-3-6 5",
  search: "M7 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM10 10l3.5 3.5",
  terminal: "M2.5 3.5h11v9h-11zM5 6.5l2 1.5-2 1.5M8.5 10h2.5",
  sidebar: "M2.5 3.5h11v9h-11zM6 3.5v9",
  dot: "M8 6.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  more: "M4 8h.01M8 8h.01M12 8h.01",
};

export function Icon({
  name,
  size = 16,
  className = "",
  title,
}: {
  name: keyof typeof PATHS | string;
  size?: number;
  className?: string;
  title?: string;
}) {
  const props = {
    width: size,
    height: size,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    className: `shrink-0 ${className}`,
  };
  const path = <path d={PATHS[name] ?? PATHS.dot} />;
  if (title) {
    return (
      <svg {...props} role="img">
        <title>{title}</title>
        {path}
      </svg>
    );
  }
  return (
    <svg {...props} aria-hidden="true">
      {path}
    </svg>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      className={`inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-[1.5px] border-current border-t-transparent ${className}`}
      role="status"
      aria-label="Running"
    />
  );
}
