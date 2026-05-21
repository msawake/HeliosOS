import * as React from "react";

interface Props {
  left: React.ReactNode;
  right: React.ReactNode;
  defaultLeftPx?: number;
  minLeft?: number;
  minRight?: number;
}

const DEFAULT_MIN = 160;

export function ResizableSplit({
  left,
  right,
  defaultLeftPx,
  minLeft = DEFAULT_MIN,
  minRight = DEFAULT_MIN,
}: Props) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [leftWidth, setLeftWidth] = React.useState<number | null>(defaultLeftPx ?? null);

  React.useLayoutEffect(() => {
    if (leftWidth === null && containerRef.current) {
      setLeftWidth(containerRef.current.offsetWidth / 2);
    }
  }, []);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = leftWidth ?? 0;
    const container = containerRef.current;

    const onMove = (ev: MouseEvent) => {
      if (!container) return;
      const delta = ev.clientX - startX;
      const maxLeft = container.offsetWidth - minRight - 5;
      setLeftWidth(Math.min(maxLeft, Math.max(minLeft, startWidth + delta)));
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <div ref={containerRef} className="flex h-full w-full overflow-hidden">
      <div
        className="flex-shrink-0 overflow-y-auto"
        style={{ width: leftWidth ?? "50%" }}
      >
        {left}
      </div>
      <div
        onMouseDown={startDrag}
        className="w-[5px] flex-shrink-0 cursor-col-resize border-r border-border hover:bg-ok/30 active:bg-ok/50"
      />
      <div className="min-w-0 flex-1 overflow-y-auto">
        {right}
      </div>
    </div>
  );
}
