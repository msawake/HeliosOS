import * as React from "react";
import { Button } from "./button";

export interface ConfirmDialogProps {
  open: boolean;
  title?: React.ReactNode;
  message?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title = "Confirm",
  message,
  confirmLabel = "OK",
  cancelLabel = "Cancel",
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  React.useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter") onConfirm();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onCancel, onConfirm]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-[1px]"
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-[420px] max-w-[92vw] border-2 border-border bg-surface shadow-[0_8px_40px_rgba(0,0,0,0.6)]"
      >
        <div className="flex items-center justify-between border-b border-border bg-bg px-3 py-2">
          <span
            className={`font-mono text-[11px] font-bold uppercase tracking-widest ${
              destructive ? "text-danger" : "text-warn"
            }`}
          >
            {destructive ? "⚠ " : ""}
            {title}
          </span>
          <button
            onClick={onCancel}
            aria-label="Close"
            className="cursor-pointer text-lg leading-none text-dim hover:text-text"
          >
            ×
          </button>
        </div>

        <div className="px-3 py-4 font-mono text-[11px] leading-relaxed text-text">
          {message}
        </div>

        <div className="flex justify-end gap-2 border-t border-border bg-bg px-3 py-2">
          <Button variant="ghost" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button
            variant={destructive ? "reject" : "ok"}
            onClick={onConfirm}
            autoFocus
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
