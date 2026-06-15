import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 whitespace-nowrap',
  {
    variants: {
      variant: {
        default: 'border-edge bg-inset text-secondary',
        brand: 'border-accent/25 bg-accent-wash text-accent',
        success: 'border-success/25 bg-success-wash text-success',
        danger: 'border-danger/25 bg-danger-wash text-danger',
        warning: 'border-warning/25 bg-warning-wash text-warning',
        outline: 'border-edge-strong bg-transparent text-tertiary',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { badgeVariants };
