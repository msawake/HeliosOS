'use client';

import { memo } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '@/lib/utils';

// GFM markdown renderer styled with the HELIOS tokens. Used for agent chat
// turns, where the model emits tables, lists, headings, and fenced code.
// react-markdown escapes raw HTML by default (no rehype-raw), so this is safe
// to point at untrusted model output.

const components: Components = {
  p: ({ children }) => <p className="my-2 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-primary">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  del: ({ children }) => <del className="text-muted line-through">{children}</del>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
    >
      {children}
    </a>
  ),
  ul: ({ children }) => (
    <ul className="my-2 ml-5 list-disc space-y-1 first:mt-0 last:mb-0 marker:text-muted">{children}</ul>
  ),
  ol: ({ children }) => (
    <ol className="my-2 ml-5 list-decimal space-y-1 first:mt-0 last:mb-0 marker:text-muted">{children}</ol>
  ),
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  h1: ({ children }) => <h1 className="mb-2 mt-4 text-base font-semibold text-primary first:mt-0">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-4 text-[15px] font-semibold text-primary first:mt-0">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-3 text-[13px] font-semibold text-primary first:mt-0">{children}</h3>,
  h4: ({ children }) => <h4 className="mb-1.5 mt-3 text-[13px] font-semibold text-secondary first:mt-0">{children}</h4>,
  h5: ({ children }) => <h5 className="mb-1 mt-2 text-xs font-semibold text-secondary first:mt-0">{children}</h5>,
  h6: ({ children }) => <h6 className="mb-1 mt-2 text-xs font-semibold text-tertiary first:mt-0">{children}</h6>,
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-edge-strong pl-3 text-secondary first:mt-0 last:mb-0">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-3 border-edge" />,
  code: ({ className, children, ...props }) => {
    // Fenced blocks arrive as <pre><code class="language-…">; inline code has
    // neither. Treat a language hint or an embedded newline as a block.
    const isBlock = /language-/.test(className ?? '') || String(children).includes('\n');
    if (isBlock) {
      return (
        <code className={cn('font-mono text-xs leading-relaxed', className)} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className="rounded border border-edge-subtle bg-inset px-1 py-0.5 font-mono text-[12px] text-secondary">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-2 overflow-x-auto rounded-lg border border-edge bg-inset px-3 py-2.5 text-secondary first:mt-0 last:mb-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="my-2 w-full overflow-x-auto first:mt-0 last:mb-0">
      <table className="w-full border-collapse text-[12px]">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="border-b border-edge">{children}</thead>,
  tbody: ({ children }) => <tbody>{children}</tbody>,
  tr: ({ children }) => <tr className="border-b border-edge-subtle last:border-0">{children}</tr>,
  th: ({ children }) => (
    <th className="px-2.5 py-1.5 text-left align-top font-medium text-tertiary">{children}</th>
  ),
  td: ({ children }) => <td className="px-2.5 py-1.5 align-top text-secondary">{children}</td>,
};

const remarkPlugins = [remarkGfm];

function MarkdownImpl({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn('text-[13px] text-primary', className)}>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={components}>
        {children.trim()}
      </ReactMarkdown>
    </div>
  );
}

/** Render trusted-but-model-authored markdown (GFM). Memoized so streaming
 *  re-renders of sibling turns don't re-parse settled bubbles. */
export const Markdown = memo(MarkdownImpl);
