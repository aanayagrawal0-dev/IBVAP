import { AlertTriangle, Info, ShieldAlert } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Severity } from "@/lib/mock-data";

const CONFIG: Record<
  Severity,
  { label: string; className: string; icon: typeof AlertTriangle }
> = {
  critical: {
    label: "CRITICAL",
    className: "border-critical text-critical bg-critical-bg",
    icon: ShieldAlert,
  },
  warning: {
    label: "WARNING",
    className: "border-warning text-warning bg-warning-bg",
    icon: AlertTriangle,
  },
  info: {
    label: "INFO",
    className: "border-info text-info bg-info-bg",
    icon: Info,
  },
};

/**
 * Severity is conveyed with an icon + text label, not color alone —
 * required for colorblind users and matches the design-skill's
 * "badge meaning cannot rely on color" guidance.
 */
export function SeverityBadge({ severity }: { severity: Severity }) {
  const { label, className, icon: Icon } = CONFIG[severity];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide2 font-mono",
        className
      )}
    >
      <Icon className="h-3 w-3" aria-hidden="true" />
      {label}
    </span>
  );
}
