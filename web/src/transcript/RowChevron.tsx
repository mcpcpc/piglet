import { Icon } from "../components/Icon";

/**
 * A transcript row's disclosure chevron. The glyphs sit inside their 12px box (› starts 4px in,
 * ⌄ 2.5px in), so the box is pulled left by 4px and the open glyph nudged back by 1.5px: the
 * drawn stroke meets the text edge in both states and the label beside it never moves.
 */
export function RowChevron({ open, className = "" }: { open: boolean; className?: string }) {
  return (
    <Icon
      name={open ? "chevronDown" : "chevronRight"}
      size={12}
      className={`-ml-1 shrink-0 ${open ? "translate-x-[1.5px]" : ""} ${className}`}
    />
  );
}
