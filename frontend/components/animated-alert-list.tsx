"use client";

import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { SeverityBadge } from "@/components/severity-badge";
import { cn } from "@/lib/utils";
import type { Alert } from "@/lib/mock-data";

/**
 * Live alert feed, magicui AnimatedList-style: new items slide/fade in at
 * the top. Honors prefers-reduced-motion by collapsing the transition to
 * a near-instant opacity swap instead of the slide.
 */
export function AnimatedAlertList({ alerts }: { alerts: Alert[] }) {
  const reduceMotion = useReducedMotion();

  return (
    <ul className="flex flex-col gap-2" aria-label="Live alerts" aria-live="polite">
      <AnimatePresence initial={false}>
        {alerts.map((alert) => (
          <motion.li
            key={alert.id}
            layout
            initial={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0.05 : 0.35, ease: [0.16, 1, 0.3, 1] }}
            className={cn(
              "rounded-md border-l-2 bg-obsidian-900/70 px-3 py-2.5",
              alert.severity === "critical" &&
                "border-l-critical shadow-[0_0_18px_-6px_rgba(255,59,48,0.5)]",
              alert.severity === "warning" && "border-l-warning",
              alert.severity === "info" && "border-l-info"
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <SeverityBadge severity={alert.severity} />
              <span className="font-mono text-[10px] text-ink-dim shrink-0">
                {alert.timestamp}
              </span>
            </div>
            <p className="mt-1.5 text-xs font-semibold text-ink">
              {alert.title}
              <span className="ml-1.5 font-normal text-ink-muted">— {alert.camera}</span>
            </p>
            <p className="mt-0.5 text-xs text-ink-muted leading-snug">{alert.description}</p>
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  );
}
