'use client';

import * as SelectPrimitive from '@radix-ui/react-select';
import { CaretDown, CaretUp, Check } from '@phosphor-icons/react';

import { cn } from '@/lib/utils';

const Select = SelectPrimitive.Root;
const SelectGroup = SelectPrimitive.Group;
const SelectValue = SelectPrimitive.Value;

function SelectTrigger({
  className,
  children,
  ...props
}: React.ComponentPropsWithRef<typeof SelectPrimitive.Trigger>) {
  return (
    <SelectPrimitive.Trigger
      data-slot="select-trigger"
      className={cn(
        'flex h-9 w-full cursor-pointer items-center justify-between gap-2 rounded-md border border-edge bg-surface px-3 py-2 text-sm text-primary',
        'transition-[border-color,box-shadow] duration-(--duration-fast)',
        'focus:outline-none focus:ring-2 focus:ring-accent/35 focus:border-accent/60',
        'data-[placeholder]:text-muted',
        'disabled:cursor-not-allowed disabled:opacity-50 disabled:bg-inset',
        'aria-invalid:border-danger/60 aria-invalid:focus:ring-danger/30',
        '[&>span]:line-clamp-1 [&>span]:text-left',
        className
      )}
      {...props}
    >
      {children}
      <SelectPrimitive.Icon asChild>
        <CaretDown className="h-3 w-3 shrink-0 text-muted" aria-hidden />
      </SelectPrimitive.Icon>
    </SelectPrimitive.Trigger>
  );
}

function SelectScrollUpButton({
  className,
  ...props
}: React.ComponentPropsWithRef<typeof SelectPrimitive.ScrollUpButton>) {
  return (
    <SelectPrimitive.ScrollUpButton
      data-slot="select-scroll-up"
      className={cn('flex cursor-default items-center justify-center py-1 text-muted', className)}
      {...props}
    >
      <CaretUp className="h-3 w-3" aria-hidden />
    </SelectPrimitive.ScrollUpButton>
  );
}

function SelectScrollDownButton({
  className,
  ...props
}: React.ComponentPropsWithRef<typeof SelectPrimitive.ScrollDownButton>) {
  return (
    <SelectPrimitive.ScrollDownButton
      data-slot="select-scroll-down"
      className={cn('flex cursor-default items-center justify-center py-1 text-muted', className)}
      {...props}
    >
      <CaretDown className="h-3 w-3" aria-hidden />
    </SelectPrimitive.ScrollDownButton>
  );
}

function SelectContent({
  className,
  children,
  position = 'popper',
  ...props
}: React.ComponentPropsWithRef<typeof SelectPrimitive.Content>) {
  return (
    <SelectPrimitive.Portal>
      <SelectPrimitive.Content
        data-slot="select-content"
        position={position}
        className={cn(
          'relative z-(--z-overlay) max-h-(--radix-select-content-available-height) min-w-[8rem] overflow-hidden rounded-lg border border-edge bg-surface p-1 shadow-lg',
          'data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[0.98]',
          'data-[state=closed]:animate-out data-[state=closed]:fade-out-0',
          'duration-150 ease-(--ease-out)',
          position === 'popper' &&
            'data-[side=bottom]:translate-y-1 data-[side=top]:-translate-y-1',
          className
        )}
        {...props}
      >
        <SelectScrollUpButton />
        <SelectPrimitive.Viewport
          className={cn(
            'p-0',
            position === 'popper' && 'w-full min-w-[var(--radix-select-trigger-width)]'
          )}
        >
          {children}
        </SelectPrimitive.Viewport>
        <SelectScrollDownButton />
      </SelectPrimitive.Content>
    </SelectPrimitive.Portal>
  );
}

function SelectLabel({
  className,
  ...props
}: React.ComponentPropsWithRef<typeof SelectPrimitive.Label>) {
  return (
    <SelectPrimitive.Label
      data-slot="select-label"
      className={cn('px-2.5 py-1.5 text-xs font-medium text-tertiary', className)}
      {...props}
    />
  );
}

function SelectItem({
  className,
  children,
  ...props
}: React.ComponentPropsWithRef<typeof SelectPrimitive.Item>) {
  return (
    <SelectPrimitive.Item
      data-slot="select-item"
      className={cn(
        'relative flex w-full cursor-pointer select-none items-center rounded-md py-1.5 pl-2.5 pr-8 text-[13px] text-secondary outline-none',
        'transition-colors duration-(--duration-fast)',
        'data-[highlighted]:bg-surface-hover data-[highlighted]:text-primary',
        'data-[state=checked]:text-primary',
        'data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
        className
      )}
      {...props}
    >
      <span className="absolute right-2.5 flex items-center justify-center text-accent">
        <SelectPrimitive.ItemIndicator>
          <Check className="h-3.5 w-3.5" aria-hidden />
        </SelectPrimitive.ItemIndicator>
      </span>
      <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
    </SelectPrimitive.Item>
  );
}

function SelectSeparator({
  className,
  ...props
}: React.ComponentPropsWithRef<typeof SelectPrimitive.Separator>) {
  return (
    <SelectPrimitive.Separator
      data-slot="select-separator"
      className={cn('-mx-1 my-1 h-px bg-edge-subtle', className)}
      {...props}
    />
  );
}

export {
  Select,
  SelectGroup,
  SelectValue,
  SelectTrigger,
  SelectContent,
  SelectLabel,
  SelectItem,
  SelectSeparator,
  SelectScrollUpButton,
  SelectScrollDownButton,
};
