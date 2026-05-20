import * as React from "react";
import { cn } from "@/lib/utils";

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  children?: React.ReactNode;
}

export function Sheet({ open, onClose, title, children }: SheetProps) {
  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  if (!open) return null;
  return (
    <div
      className={cn(
        "fixed right-0 top-0 z-50 h-screen w-[440px] overflow-y-auto border-l-2 border-border bg-surface p-4 shadow-[-4px_0_20px_rgba(0,0,0,0.4)]",
      )}
    >
      <button
        onClick={onClose}
        className="absolute right-3 top-2 cursor-pointer text-lg text-dim hover:text-text"
        aria-label="Close"
      >
        ×
      </button>
      {title && <h3 className="mb-3 text-[14px] text-bright">{title}</h3>}
      {children}
    </div>
  );
}
