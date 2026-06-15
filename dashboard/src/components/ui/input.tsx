import { cn } from '@/lib/utils';

const fieldBase =
  'flex w-full rounded-md border border-edge bg-surface px-3 text-sm text-primary ' +
  'placeholder:text-muted transition-[border-color,box-shadow] duration-(--duration-fast) ' +
  'focus:outline-none focus:ring-2 focus:ring-accent/35 focus:border-accent/60 ' +
  'disabled:cursor-not-allowed disabled:opacity-50 disabled:bg-inset ' +
  'aria-invalid:border-danger/60 aria-invalid:focus:ring-danger/30';

function Input({
  className,
  type,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { ref?: React.Ref<HTMLInputElement> }) {
  return (
    <input data-slot="input" type={type} className={cn(fieldBase, 'h-9 py-2', className)} {...props} />
  );
}

function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement> & {
  ref?: React.Ref<HTMLTextAreaElement>;
}) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(fieldBase, 'min-h-[80px] py-2 resize-y', className)}
      {...props}
    />
  );
}

function Label({
  className,
  ...props
}: React.LabelHTMLAttributes<HTMLLabelElement> & { ref?: React.Ref<HTMLLabelElement> }) {
  return (
    <label data-slot="label" className={cn('text-[13px] font-medium text-secondary', className)} {...props} />
  );
}

function Select({
  className,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement> & { ref?: React.Ref<HTMLSelectElement> }) {
  return (
    <select
      data-slot="select"
      className={cn(
        fieldBase,
        'h-9 py-2 appearance-none cursor-pointer',
        "bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%2212%22%20height%3D%2212%22%20fill%3D%22none%22%20stroke%3D%22%23716b5d%22%20stroke-width%3D%221.5%22%3E%3Cpath%20d%3D%22m2%204%204%204%204-4%22%2F%3E%3C%2Fsvg%3E')] bg-[length:12px] bg-[right_12px_center] bg-no-repeat pr-9",
        className
      )}
      {...props}
    />
  );
}

export { Input, Textarea, Label, Select };
