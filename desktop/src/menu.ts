// The application menu. Windows and Linux never show it (the window has no native title bar), so
// there it only carries keyboard shortcuts; macOS shows it in the menu bar.

import type { MenuItemConstructorOptions } from "electron";

/**
 * Electron's default menu minus the shortcuts that are easy to hit while typing in the composer
 * and that throw work away. Reload and force reload (Ctrl/⌘+R, +Shift+R) are gone everywhere.
 * On Windows and Linux, where nothing shows the menu, so are close window (Ctrl+W, a terminal's
 * delete-word, which quits piglet), quit (Ctrl+Q) and minimize (Ctrl+M, a terminal's Enter).
 * macOS keeps ⌘W, ⌘Q and ⌘M, which its menu bar lists and every Mac app has. Editing, zoom,
 * full screen and developer tools stay on every platform.
 */
export function menuTemplate(platform: NodeJS.Platform): MenuItemConstructorOptions[] {
  const macOnly = (...items: MenuItemConstructorOptions[]) => (platform === "darwin" ? items : []);
  return [
    ...macOnly({ role: "appMenu" }, { role: "fileMenu" }),
    { role: "editMenu" },
    {
      label: "View",
      submenu: [
        { role: "resetZoom" },
        { role: "zoomIn" },
        { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
        { role: "toggleDevTools" },
      ],
    },
    ...macOnly({ role: "windowMenu" }),
  ];
}
