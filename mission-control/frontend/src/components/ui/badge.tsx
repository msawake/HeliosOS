import * as React from "react";
import { cn } from "@/lib/utils";

export function Badge({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-block rounded-sm px-[7px] py-[2px] text-[10px] font-semibold",
        className,
      )}
      {...props}
    />
  );
}
