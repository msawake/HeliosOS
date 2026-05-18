interface StatCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  color?: 'brand' | 'green' | 'violet' | 'teal' | 'amber' | 'red' | 'default';
}

const COLOR_MAP: Record<string, string> = {
  brand: 'text-[#10A37F]',
  green: 'text-emerald-600',
  violet: 'text-violet-600',
  teal: 'text-teal-600',
  amber: 'text-amber-600',
  red: 'text-red-600',
  default: 'text-[#0d0d0d]',
};

export function StatCard({ title, value, subtitle, color = 'default' }: StatCardProps) {
  return (
    <div className="card" data-testid={`stat-${title.toLowerCase().replace(/\s+/g, '-')}`}>
      <p className="text-[13px] font-medium text-[#6e6e80]">{title}</p>
      <p className={`text-3xl font-semibold mt-1 ${COLOR_MAP[color] || COLOR_MAP.default}`}>
        {value}
      </p>
      {subtitle && <p className="text-xs text-[#8e8ea0] mt-1">{subtitle}</p>}
    </div>
  );
}
