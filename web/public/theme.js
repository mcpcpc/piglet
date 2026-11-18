// Apply a saved Light or Dark theme before first paint, so it doesn't flash in after settings
// load. A separate file because the page's CSP forbids inline scripts. applyTheme in
// src/state/store.ts writes the value; "system" leaves the OS preference in charge.
(() => {
  try {
    const theme = localStorage.getItem("piglet:theme");
    if (theme !== "light" && theme !== "dark") return;
    document.documentElement.dataset.theme = theme;
    const color = theme === "dark" ? "#161616" : "#ffffff";
    for (const meta of document.querySelectorAll('meta[name="theme-color"]')) meta.content = color;
  } catch {
    // Storage can be unavailable; the theme then applies once settings load.
  }
})();
