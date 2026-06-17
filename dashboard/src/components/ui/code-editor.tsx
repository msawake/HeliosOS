'use client';

import { useLayoutEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';

const INDENT = '  ';

/**
 * Lightweight code editor: a controlled <textarea> with a synced line-number
 * gutter, tab/shift-tab (de)indent, YAML-aware auto-indent on Enter, current-line
 * highlight, and an optional error line. No external editor dependency.
 */
export function CodeEditor({
  value,
  onChange,
  errorLine = null,
  ariaLabel,
  className,
}: {
  value: string;
  onChange: (v: string) => void;
  /** 1-based line to flag (e.g. a parse error). */
  errorLine?: number | null;
  ariaLabel?: string;
  className?: string;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const pendingSel = useRef<[number, number] | null>(null);
  const [currentLine, setCurrentLine] = useState(1);

  const lines = value.length ? value.split('\n') : [''];

  // Restore the caret after a programmatic edit (tab/enter), then resync the
  // active-line marker — the textarea's value has updated by this point.
  useLayoutEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    if (pendingSel.current) {
      const [s, e] = pendingSel.current;
      ta.setSelectionRange(s, e);
      pendingSel.current = null;
    }
    setCurrentLine(value.slice(0, ta.selectionStart).split('\n').length);
  }, [value]);

  const syncScroll = () => {
    if (gutterRef.current && taRef.current) {
      gutterRef.current.scrollTop = taRef.current.scrollTop;
    }
  };

  const refreshCurrentLine = () => {
    const ta = taRef.current;
    if (ta) setCurrentLine(value.slice(0, ta.selectionStart).split('\n').length);
  };

  // Edit + queue the resulting caret position for the layout effect above.
  const apply = (next: string, selStart: number, selEnd = selStart) => {
    pendingSel.current = [selStart, selEnd];
    onChange(next);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const ta = taRef.current;
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;

    if (e.key === 'Tab') {
      e.preventDefault();
      const lineStart = value.lastIndexOf('\n', start - 1) + 1;

      // Plain insert when there's no selection and we're indenting.
      if (start === end && !e.shiftKey) {
        apply(value.slice(0, start) + INDENT + value.slice(end), start + INDENT.length);
        return;
      }

      // Otherwise (de)indent every line the selection touches.
      const eol = value.indexOf('\n', end);
      const blockEnd = eol === -1 ? value.length : eol;
      const blockLines = value.slice(lineStart, blockEnd).split('\n');

      if (e.shiftKey) {
        let cutFirst = 0;
        let cutTotal = 0;
        const dedented = blockLines.map((ln, i) => {
          const m = ln.match(/^( {1,2}|\t)/);
          const cut = m ? m[0].length : 0;
          if (i === 0) cutFirst = cut;
          cutTotal += cut;
          return ln.slice(cut);
        });
        const next = value.slice(0, lineStart) + dedented.join('\n') + value.slice(blockEnd);
        apply(next, Math.max(lineStart, start - cutFirst), Math.max(lineStart, end - cutTotal));
      } else {
        const indented = blockLines.map((ln) => INDENT + ln);
        const next = value.slice(0, lineStart) + indented.join('\n') + value.slice(blockEnd);
        apply(next, start + INDENT.length, end + INDENT.length * blockLines.length);
      }
      return;
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      // Carry the current line's indentation; add one level after a `key:` line.
      e.preventDefault();
      const lineStart = value.lastIndexOf('\n', start - 1) + 1;
      const curLine = value.slice(lineStart, start);
      let indent = (curLine.match(/^[ \t]*/) ?? [''])[0];
      if (/[:>|-]\s*$/.test(curLine)) indent += INDENT;
      const insert = '\n' + indent;
      apply(value.slice(0, start) + insert + value.slice(end), start + insert.length);
      return;
    }
  };

  return (
    <div
      className={cn(
        'flex overflow-hidden rounded-md border border-edge bg-surface',
        'transition-[border-color,box-shadow] duration-(--duration-fast)',
        'focus-within:ring-2 focus-within:ring-accent/35 focus-within:border-accent/60',
        errorLine ? 'border-danger/50' : '',
        className
      )}
    >
      <div
        ref={gutterRef}
        aria-hidden
        className="shrink-0 overflow-hidden border-r border-edge-subtle bg-inset/40 py-3 font-mono text-xs leading-5 text-muted"
      >
        {lines.map((_, i) => {
          const n = i + 1;
          return (
            <div
              key={n}
              className={cn(
                'px-2.5 text-right tabular-nums',
                n === currentLine && 'bg-surface-hover text-secondary',
                n === errorLine && 'bg-danger-wash font-medium text-danger'
              )}
            >
              {n}
            </div>
          );
        })}
      </div>
      <textarea
        ref={taRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onKeyUp={refreshCurrentLine}
        onClick={refreshCurrentLine}
        onScroll={syncScroll}
        spellCheck={false}
        autoCapitalize="off"
        autoCorrect="off"
        aria-label={ariaLabel}
        className="block h-[30rem] w-full resize-none bg-transparent px-3 py-3 font-mono text-xs leading-5 text-primary outline-none placeholder:text-muted"
      />
    </div>
  );
}
