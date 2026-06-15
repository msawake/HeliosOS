'use client';

import { Check, CopySimple } from '@phosphor-icons/react';

import { useCopy } from '@/lib/use-copy';
import { cn } from '@/lib/utils';

// Mono block with copy. Used for system prompts, manifests, and payloads.
// No syntax-highlighting dependency; the ink/paper contrast carries it.

interface CodeBlockProps {
  code: string;
  /** Small caption above the block, e.g. "System prompt". */
  label?: string;
  /** Cap height and scroll for long payloads. */
  maxHeight?: number;
  wrap?: boolean;
  className?: string;
}

export function CodeBlock({ code, label, maxHeight, wrap = false, className }: CodeBlockProps) {
  const { copied, copy } = useCopy();

  return (
    <div data-slot="code-block" className={cn('group relative', className)}>
      {label ? <p className="mb-1.5 text-xs font-medium text-tertiary">{label}</p> : null}
      <div className="relative rounded-lg border border-edge bg-inset">
        <pre
          className={cn(
            'overflow-x-auto px-4 py-3 font-mono text-xs leading-relaxed text-secondary',
            wrap && 'whitespace-pre-wrap break-words'
          )}
          style={maxHeight ? { maxHeight, overflowY: 'auto' } : undefined}
        >
          <code>{code}</code>
        </pre>
        <button
          type="button"
          onClick={() => copy(code)}
          aria-label={copied ? 'Copied' : 'Copy to clipboard'}
          className={cn(
            'absolute right-2 top-2 cursor-pointer rounded-md border border-edge bg-surface p-1.5 text-tertiary opacity-0 shadow-xs transition-all duration-(--duration-fast) group-hover:opacity-100 focus-visible:opacity-100 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/55',
            copied && 'opacity-100 text-accent border-accent/40'
          )}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <CopySimple className="h-3.5 w-3.5" aria-hidden />
          )}
        </button>
      </div>
    </div>
  );
}
