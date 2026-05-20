import * as React from "react";
import { cn } from "@/lib/utils";

export const Table = ({ className, ...p }: React.HTMLAttributes<HTMLTableElement>) => (
  <table className={cn("w-full border-collapse", className)} {...p} />
);
export const Thead = (p: React.HTMLAttributes<HTMLTableSectionElement>) => <thead {...p} />;
export const Tbody = (p: React.HTMLAttributes<HTMLTableSectionElement>) => <tbody {...p} />;
export const Tr = ({ className, ...p }: React.HTMLAttributes<HTMLTableRowElement>) => (
  <tr className={cn("hover:bg-info/5 cursor-pointer", className)} {...p} />
);
export const Th = ({ className, ...p }: React.ThHTMLAttributes<HTMLTableCellElement>) => (
  <th
    className={cn(
      "sticky top-0 z-10 border-b border-border bg-surface px-[10px] py-[6px] text-left text-[9px] uppercase tracking-wider text-dim",
      className,
    )}
    {...p}
  />
);
export const Td = ({ className, ...p }: React.TdHTMLAttributes<HTMLTableCellElement>) => (
  <td
    className={cn("whitespace-nowrap border-b border-border px-[10px] py-[5px]", className)}
    {...p}
  />
);
