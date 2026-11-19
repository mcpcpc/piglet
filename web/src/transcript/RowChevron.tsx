import { Icon } from "../components/Icon";

/**
 * A transcript row's disclosure chevron. Like every leading glyph in the transcript it is a 12px
 * box at the content edge with its drawing centred in it, so › and ⌄ share a centre line 6px in
 * with Stopped, a failed line and the working spinner, and the label after the row's `gap-2`
 * starts 20px in whichever way it points.
 */
export function RowChevron({ open, className = "" }: { open: boolean; className?: string }) {
  return <Icon name={open ? "chevronDown" : "chevronRight"} size={12} className={className} />;
}
