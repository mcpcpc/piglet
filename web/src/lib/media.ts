// Media queries shared by the responsive layout. The widths mirror Tailwind's lg and xl
// breakpoints, so `max-lg:` / `max-xl:` classes and these queries switch at the same pixel.

import { useSyncExternalStore } from "react";

/** Below Tailwind's lg: the sidebar becomes an off-canvas panel. */
export const OVERLAY_SIDEBAR = "(max-width: 1023.98px)";
/** Below Tailwind's xl: the Changes/Bash drawer becomes an off-canvas panel. */
export const OVERLAY_DRAWER = "(max-width: 1279.98px)";
/** A touch-first device, where Enter should not send and hover never happens. */
export const COARSE_POINTER = "(pointer: coarse)";

/** Whether `query` matches now; false where `matchMedia` is unavailable (tests, SSR). */
export function matches(query: string): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(query).matches
    : false;
}

/** Subscribe a component to a media query. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => matches(query),
    () => false,
  );
}
