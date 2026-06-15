import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 whitespace-nowrap text-sm font-medium cursor-pointer select-none transition-[background-color,border-color,color,transform,box-shadow] duration-(--duration-fast) ease-(--ease-press) active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/55 focus-visible:ring-offset-2 focus-visible:ring-offset-page disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        // Primary: ink on paper. Gold stays an accent, never a fill.
        default: 'bg-ink text-paper hover:bg-ink-hover shadow-xs',
        secondary:
          'bg-surface text-primary border border-edge hover:bg-surface-hover hover:border-edge-strong shadow-xs',
        destructive:
          'bg-danger-wash text-danger border border-danger/25 hover:border-danger/50 hover:bg-danger/15',
        ghost: 'text-secondary hover:bg-surface-hover hover:text-primary',
        link: 'text-accent underline-offset-4 hover:underline hover:text-accent-hover',
        outline:
          'border border-edge bg-transparent text-secondary hover:bg-surface-hover hover:text-primary',
      },
      size: {
        default: 'h-9 px-4 rounded-md',
        sm: 'h-8 px-3 rounded-md text-[13px]',
        lg: 'h-11 px-6 rounded-lg text-base',
        icon: 'h-9 w-9 rounded-md',
        'icon-sm': 'h-8 w-8 rounded-md',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
  ref?: React.Ref<HTMLButtonElement>;
}

function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button';
  return (
    <Comp
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
