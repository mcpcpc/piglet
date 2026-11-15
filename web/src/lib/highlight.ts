// Lazy Shiki highlighter for the live app (the HTML export renders plain code blocks).

import type { Highlighter } from "../transcript/Markdown";

type ShikiModule = typeof import("shiki");
let shiki: Promise<ShikiModule> | null = null;

export const shikiHighlighter: Highlighter = async (code, lang) => {
  shiki ??= import("shiki");
  const module = await shiki;
  if (!(lang in module.bundledLanguages)) return null;
  return module.codeToHtml(code, {
    lang,
    themes: { light: "github-light", dark: "github-dark" },
    defaultColor: "light",
  });
};
