import { cn } from '@/lib/utils';

import { Label } from './input';

// Form composition layer: label above input, helper text in markup, error
// below. Pairs with the native Input/Textarea/Select primitives; ids are the
// caller's responsibility (htmlFor/id).

function Field({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div data-slot="field" className={cn('flex flex-col gap-2', className)} {...props} />;
}

function FieldDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p data-slot="field-description" className={cn('text-xs text-tertiary', className)} {...props} />
  );
}

function FieldError({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLParagraphElement>) {
  if (!children) return null;
  return (
    <p data-slot="field-error" role="alert" className={cn('text-xs text-danger', className)} {...props}>
      {children}
    </p>
  );
}

export { Field, Label as FieldLabel, FieldDescription, FieldError };
