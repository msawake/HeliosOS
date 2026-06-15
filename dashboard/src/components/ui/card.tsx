import { cn } from '@/lib/utils';

type DivProps = React.HTMLAttributes<HTMLDivElement>;

function Card({ className, ...props }: DivProps) {
  return (
    <div
      data-slot="card"
      className={cn('rounded-lg border border-edge bg-surface shadow-xs', className)}
      {...props}
    />
  );
}

function CardHeader({ className, ...props }: DivProps) {
  return (
    <div data-slot="card-header" className={cn('flex flex-col gap-1 px-5 py-4', className)} {...props} />
  );
}

function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3 data-slot="card-title" className={cn('text-sm font-medium text-primary', className)} {...props} />
  );
}

function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p data-slot="card-description" className={cn('text-[13px] text-tertiary', className)} {...props} />
  );
}

function CardContent({ className, ...props }: DivProps) {
  return <div data-slot="card-content" className={cn('px-5 pb-5', className)} {...props} />;
}

function CardFooter({ className, ...props }: DivProps) {
  return (
    <div
      data-slot="card-footer"
      className={cn('flex items-center gap-2 border-t border-edge-subtle px-5 py-3', className)}
      {...props}
    />
  );
}

export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter };
