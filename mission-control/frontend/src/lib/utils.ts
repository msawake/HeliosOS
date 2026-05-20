import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fmt(n: number | undefined | null): string {
  const v = n ?? 0;
  if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (v >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return String(v);
}

export function usd(n: number | undefined | null): string {
  const v = n ?? 0;
  return "$" + (v < 1 ? v.toFixed(4) : v.toFixed(2));
}

export function ago(iso: string | undefined | null): string {
  if (!iso) return "-";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return "now";
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  return Math.floor(s / 3600) + "h";
}

export function shortName(n: string | undefined | null): string {
  return (n ?? "").split("/").pop() ?? "";
}
