import { createContext, memo, useContext, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Icon } from "../components/Icon";

/** Returns highlighted HTML for a code block, or null when highlighting is unavailable. */
export type Highlighter = (code: string, lang: string) => Promise<string | null>;

export const HighlighterContext = createContext<Highlighter | null>(null);

type CopyState = "idle" | "copied" | "failed";

const COPY_UI: Record<CopyState, { label: string; text: string; icon: string; tone: string }> = {
  idle: { label: "Copy code", text: "Copy", icon: "copy", tone: "text-fg-muted hover:text-fg" },
  copied: { label: "Copied", text: "Copied", icon: "check", tone: "text-fg" },
  failed: { label: "Copy failed", text: "Copy failed", icon: "alert", tone: "text-danger" },
};

/** Copies `text` to the clipboard and confirms in place for a moment. */
function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    let next: CopyState = "copied";
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      next = "failed";
    }
    setState(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1500);
  };
  const ui = COPY_UI[state];
  return (
    <button
      type="button"
      onClick={() => void copy()}
      aria-label={ui.label}
      className={`ml-auto flex h-6 shrink-0 items-center gap-1 rounded px-1.5 hover:bg-hover focus-visible:-outline-offset-2 pointer-coarse:h-8 ${ui.tone}`}
    >
      <Icon name={ui.icon} size={12} />
      {ui.text}
    </button>
  );
}

/** A fenced code block: a header with the fence's language and a copy button, then the code. */
export function CodeBlock({ code, lang }: { code: string; lang: string }) {
  return (
    <div className="code-block">
      <div className="flex h-7 select-none items-center gap-2 border-line border-b pr-1 pl-2.5 text-[11px] pointer-coarse:h-8">
        {lang ? <span className="min-w-0 truncate text-fg-faint">{lang}</span> : null}
        <CopyButton text={code} />
      </div>
      <HighlightedCode code={code} lang={lang} />
    </div>
  );
}

function HighlightedCode({ code, lang }: { code: string; lang: string }) {
  const highlight = useContext(HighlighterContext);
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    if (highlight && lang) {
      highlight(code, lang)
        .then((result) => {
          if (!cancelled) setHtml(result);
        })
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, [code, lang, highlight]);
  if (html) {
    // Shiki output is generated from the code text, not from untrusted HTML.
    // biome-ignore lint/security/noDangerouslySetInnerHtml: trusted highlighter output
    return <div dangerouslySetInnerHTML={{ __html: html }} />;
  }
  return (
    <pre>
      <code>{code}</code>
    </pre>
  );
}

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-piglet">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          pre: ({ children }) => <>{children}</>,
          code: ({ className, children }) => {
            const lang = /language-(\S+)/.exec(className ?? "")?.[1];
            const code = String(children ?? "");
            if (!lang && !code.includes("\n")) return <code>{children}</code>;
            return <CodeBlock code={code.replace(/\n$/, "")} lang={lang ?? ""} />;
          },
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
});
