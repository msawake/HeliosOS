import { cn } from '@/lib/utils';

// Helios OS mark: the Helios sun-chariot seal (letterpress woodcut). It's a
// dark-ink stamp on a light field, so we drop the white with `mix-blend-multiply`
// to sit cleanly on any of the app's paper-toned surfaces. The asset lives in
// /public and is the same artwork used for the favicon (src/app/icon.png).

export function LogoMark({ className }: { className?: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static brand mark, no optimization needed
    <img
      src="/helios-mark.png"
      alt=""
      aria-hidden
      className={cn('select-none object-contain mix-blend-multiply', className)}
    />
  );
}

export function Logo({
  size = 'default',
  className,
}: {
  size?: 'sm' | 'default' | 'lg';
  className?: string;
}) {
  const dims = size === 'sm' ? 'w-6 h-6' : size === 'lg' ? 'w-10 h-10' : 'w-8 h-8';

  return (
    <div className={cn('flex items-center gap-2.5', className)}>
      <LogoMark className={dims} />
      <span
        className={cn(
          'font-display font-semibold tracking-[0.04em] text-primary',
          size === 'sm' && 'text-sm',
          size === 'default' && 'text-base',
          size === 'lg' && 'text-xl'
        )}
      >
        Helios OS
      </span>
    </div>
  );
}
