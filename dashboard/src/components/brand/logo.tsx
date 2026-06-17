import { cn } from '@/lib/utils';

// Helios OS mark: an anvil reduced to solid ink (letterpress flatness) with a
// trio of gold sparks struck off its face — the forge's single light against
// the ink, the same role gold plays everywhere else. Coordinates are static
// (no Math.random / Date), so server and client serialize identically.

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden
    >
      {/* Anvil — face + horn, waist, and splayed foot, in solid ink. */}
      <path
        d="M16 47 L33 43 L74 43 L74 51 L33 51 Z"
        fill="currentColor"
        className="text-primary"
      />
      <rect x="44" y="51" width="14" height="9" fill="currentColor" className="text-primary" />
      <path
        d="M34 60 L68 60 L73 69 L29 69 Z"
        fill="currentColor"
        className="text-primary"
      />
      {/* Sparks struck off the face — the gold accent, rationed. */}
      <g stroke="currentColor" strokeWidth="5" strokeLinecap="round" className="text-ray">
        <line x1="56" y1="40" x2="59" y2="29" />
        <line x1="66" y1="40" x2="74" y2="31" />
        <line x1="47" y1="40" x2="46" y2="32" />
      </g>
      <circle cx="71" cy="25" r="3.4" fill="currentColor" className="text-ray" />
    </svg>
  );
}

export function Logo({
  size = 'default',
  className,
}: {
  size?: 'sm' | 'default' | 'lg';
  className?: string;
}) {
  const dims = size === 'sm' ? 'w-5 h-5' : size === 'lg' ? 'w-9 h-9' : 'w-7 h-7';

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
