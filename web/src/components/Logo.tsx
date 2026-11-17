// piglet's mark inside the UI: an outlined snout, colored by the surrounding text color. The app
// icon and favicons use the solid snout instead; scripts/gen_icons.py draws them on the same grid.

/** The outlined snout. Decorative: it always sits next to the word "piglet". */
export function Logo({ size = 18, className = "" }: { size?: number; className?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      aria-hidden="true"
      className={`shrink-0 ${className}`}
    >
      <rect
        x="7"
        y="16"
        width="50"
        height="32"
        rx="16"
        fill="none"
        stroke="currentColor"
        strokeWidth={5}
      />
      <ellipse cx="25" cy="32" rx="3.5" ry="5" fill="currentColor" />
      <ellipse cx="39" cy="32" rx="3.5" ry="5" fill="currentColor" />
    </svg>
  );
}
