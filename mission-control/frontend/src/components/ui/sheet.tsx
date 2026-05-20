import * as React from "react";
import { cn } from "@/lib/utils";

const MIN_WIDTH = 280;
const MAX_WIDTH = 900;
const DEFAULT_WIDTH = 440;

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  children?: React.ReactNode;
}

export function Sheet({ open, onClose, title, children }: SheetProps) {
  const [width, setWidth] = React.useState(DEFAULT_WIDTH);
  const dragging = React.useRef(false);
  const startX = React.useRef(0);
  const startWidth = React.useRef(DEFAULT_WIDTH);

  React.useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  const onMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    startX.current = e.clientX;
    startWidth.current = width;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const delta = startX.current - ev.clientX;
      setWidth(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta)));
    };
    const onUp = () => {
      dragging.current = false;
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  if (!open) return null;
  return (
    <div
      className={cn(
        "fixed right-0 top-0 z-50 h-screen overflow-y-auto border-l-2 border-border bg-surface p-4 shadow-[-4px_0_20px_rgba(0,0,0,0.4)]",
      )}
      style={{ width }}
    >
      {/* drag handle */}
      <div
        onMouseDown={onMouseDown}
        className="absolute left-0 top-0 h-full w-[5px] cursor-col-resize hover:bg-ok/30 active:bg-ok/50"
        title="Drag to resize"
      />
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
