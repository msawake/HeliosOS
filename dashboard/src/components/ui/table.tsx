import { cn } from '@/lib/utils';

// Presentational table family. Numerics go mono+tabular via TableCellMono.

function Table({
  className,
  wrapperClassName,
  ...props
}: React.TableHTMLAttributes<HTMLTableElement> & { wrapperClassName?: string }) {
  return (
    <div data-slot="table-wrapper" className={cn('w-full overflow-x-auto', wrapperClassName)}>
      <table data-slot="table" className={cn('w-full caption-bottom text-[13px]', className)} {...props} />
    </div>
  );
}

function TableHeader({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <thead data-slot="table-header" className={cn('border-b border-edge', className)} {...props} />;
}

function TableBody({ className, ...props }: React.HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody data-slot="table-body" className={className} {...props} />;
}

function TableRow({ className, ...props }: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      data-slot="table-row"
      className={cn(
        'border-b border-edge-subtle transition-colors duration-(--duration-fast) last:border-0 hover:bg-surface-hover/60',
        className
      )}
      {...props}
    />
  );
}

function TableHead({ className, ...props }: React.ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      data-slot="table-head"
      className={cn(
        'h-9 whitespace-nowrap px-4 text-left align-middle text-xs font-medium text-tertiary',
        className
      )}
      {...props}
    />
  );
}

function TableCell({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      data-slot="table-cell"
      className={cn('whitespace-nowrap px-4 py-3 align-middle text-secondary', className)}
      {...props}
    />
  );
}

/** Numeric/data cell: mono, tabular. */
function TableCellMono({ className, ...props }: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td
      data-slot="table-cell-mono"
      className={cn('whitespace-nowrap px-4 py-3 align-middle font-mono text-xs text-secondary', className)}
      {...props}
    />
  );
}

export { Table, TableHeader, TableBody, TableRow, TableHead, TableCell, TableCellMono };
