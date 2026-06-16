import { useState } from 'react';
import { X } from '@phosphor-icons/react';

import { Field, FieldLabel, FieldDescription, FieldError } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/** Field wrapper: label + optional description + control + error. */
export function LabeledField({
  label,
  htmlFor,
  description,
  error,
  children,
  className,
}: {
  label: string;
  htmlFor?: string;
  description?: string;
  error?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Field className={className}>
      <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>
      {description ? <FieldDescription>{description}</FieldDescription> : null}
      {children}
      {error ? <FieldError>{error}</FieldError> : null}
    </Field>
  );
}

/** Editable list of short string values rendered as removable chips. */
export function ChipsInput({
  values,
  onChange,
  placeholder,
  ariaLabel,
}: {
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  ariaLabel?: string;
}) {
  const [draft, setDraft] = useState('');
  const add = () => {
    const v = draft.trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setDraft('');
  };
  return (
    <div className="space-y-2">
      {values.length ? (
        <div className="flex flex-wrap gap-1.5">
          {values.map((v) => (
            <Badge key={v} variant="default" className="gap-1">
              <span className="font-mono">{v}</span>
              <button
                type="button"
                aria-label={`Remove ${v}`}
                onClick={() => onChange(values.filter((x) => x !== v))}
                className="text-muted hover:text-danger"
              >
                <X className="h-3 w-3" aria-hidden />
              </button>
            </Badge>
          ))}
        </div>
      ) : null}
      <Input
        value={draft}
        aria-label={ariaLabel}
        placeholder={placeholder ?? 'Type and press Enter'}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
    </div>
  );
}

/** A labeled checkbox row styled with the design tokens (no Switch primitive). */
export function CheckboxRow({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2.5">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className={cn('mt-0.5 h-4 w-4 cursor-pointer rounded border-edge accent-accent')}
      />
      <span>
        <span className="text-[13px] font-medium text-secondary">{label}</span>
        {description ? <span className="block text-xs text-tertiary">{description}</span> : null}
      </span>
    </label>
  );
}
