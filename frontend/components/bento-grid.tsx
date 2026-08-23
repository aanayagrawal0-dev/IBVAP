import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

export function BentoGrid({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("grid grid-cols-1 gap-4 md:grid-cols-3", className)}>
      {children}
    </div>
  );
}

export function BentoCard({
  children,
  className,
  span = 1,
}: {
  children: ReactNode;
  className?: string;
  span?: 1 | 2 | 3;
}) {
  const spanClass =
    span === 3 ? "md:col-span-3" : span === 2 ? "md:col-span-2" : "md:col-span-1";

  return (
    <div
      className={cn(
        "rounded-lg border border-obsidian-border bg-obsidian-900/60 p-5 shadow-panel",
        spanClass,
        className
      )}
    >
      {children}
    </div>
  );
}
