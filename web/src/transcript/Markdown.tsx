import { createContext, memo, useContext, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Returns highlighted HTML for a code block, or null when highlighting is unavailable. */
export type Highlighter = (code: string, lang: string) => Promise<string | null>;

export const HighlighterContext = createContext<Highlighter | null>(null);

export function CodeBlock({ code, lang }: { code: string; lang: string }) {
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
