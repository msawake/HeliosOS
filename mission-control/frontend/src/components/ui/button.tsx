import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center rounded-sm font-mono text-[10px] font-semibold transition-colors focus-visible:outline-none disabled:opacity-50 disabled:pointer-events-none",
  {
    variants: {
      variant: {
        default:
          "border border-border bg-bg text-text hover:border-info hover:text-info",
        danger:
          "border border-border bg-bg text-text hover:border-danger hover:text-danger",
        ok: "border border-ok text-ok bg-bg hover:bg-ok/10",
        reject: "border border-danger text-danger bg-bg hover:bg-danger/10",
        ghost: "text-text hover:bg-border/40",
      },
      size: {
        default: "px-2 py-[3px]",
        sm: "px-[6px] py-[2px] text-[9px]",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { buttonVariants };
