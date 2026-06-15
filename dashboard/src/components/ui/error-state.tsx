'use client';

import { ArrowsClockwise, WarningCircle } from '@phosphor-icons/react';
import { useRouter } from 'next/navigation';
import { useState, useTransition } from 'react';

import { cn } from '@/lib/utils';

import { Button } from './button';

// The replacement for redirect("/login")-on-error. A failed data fetch
// renders this, in place, with a real retry. Pass `onRetry` for client-fetched
// views (the common case here); otherwise it falls back to router.refresh().
// A data error NEVER logs anyone out.

interface ErrorStateProps extends React.HTMLAttributes<HTMLDivElement> {
  title?: string;
  detail?: string;
  onRetry?: () => void | Promise<void>;
  /** Inline variant for a single card/section inside an otherwise healthy page. */
  compact?: boolean;
}

export function ErrorState({
  title = "Couldn't load this data",
  detail,
  onRetry,
  compact = false,
  className,
  ...props
}: ErrorStateProps) {
  const router = useRouter();
  const [refreshing, startTransition] = useTransition();
  const [running, setRunning] = useState(false);
  const pending = refreshing || running;

  const retry = () => {
    if (onRetry) {
      const r = onRetry();
      if (r instanceof Promise) {
        setRunning(true);
        r.finally(() => setRunning(false));
      }
      return;
    }
    startTransition(() => router.refresh());
  };

  if (compact) {
    return (
      <div
        role="alert"
        data-slot="error-state"
        className={cn(
          'flex h-full min-h-20 flex-col items-start justify-center gap-2 rounded-lg border border-danger/20 bg-danger-wash px-4 py-3',
          className
        )}
        {...props}
      >
        <p className="flex items-center gap-1.5 text-[13px] font-medium text-danger">
          <WarningCircle className="h-4 w-4 shrink-0" aria-hidden />
          {title}
        </p>
        <button
          onClick={retry}
          disabled={pending}
          className="cursor-pointer text-xs font-medium text-secondary underline-offset-2 hover:underline disabled:opacity-50"
        >
          {pending ? 'Retrying…' : 'Retry'}
        </button>
      </div>
    );
  }

  return (
    <div
      role="alert"
      data-slot="error-state"
      className={cn(
        'flex flex-col items-center justify-center gap-1 rounded-lg border border-edge px-6 py-14 text-center',
        className
      )}
      {...props}
    >
      <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-danger-wash">
        <WarningCircle className="h-5 w-5 text-danger" aria-hidden />
      </span>
      <p className="text-sm font-medium text-primary">{title}</p>
      <p className="max-w-sm text-[13px] text-tertiary">
        {detail || "The request didn't go through. Your session is fine, this is on our side."}
      </p>
      <Button variant="secondary" size="sm" className="mt-4" onClick={retry} disabled={pending}>
        <ArrowsClockwise className={cn('h-3.5 w-3.5', pending && 'animate-spin')} aria-hidden />
        {pending ? 'Retrying…' : 'Retry'}
      </Button>
    </div>
  );
}
