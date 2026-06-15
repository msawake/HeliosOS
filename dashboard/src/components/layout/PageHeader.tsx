import Link from 'next/link';
import { CaretLeft } from '@phosphor-icons/react';

interface PageHeaderProps {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  back?: { href: string; label: string };
}

export function PageHeader({ title, description, actions, back }: PageHeaderProps) {
  return (
    <div className="mb-6">
      {back ? (
        <Link
          href={back.href}
          className="mb-2 inline-flex items-center gap-1 text-[13px] text-tertiary transition-colors hover:text-accent"
        >
          <CaretLeft className="h-3.5 w-3.5" aria-hidden />
          {back.label}
        </Link>
      ) : null}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold leading-tight text-primary">{title}</h1>
          {description ? <p className="mt-1 text-sm text-tertiary">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
