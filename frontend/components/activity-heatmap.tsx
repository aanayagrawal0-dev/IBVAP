import { Fragment } from "react";
import { activityDensity } from "@/lib/mock-data";

export function ActivityHeatmap() {
  return (
    <div>
      <div
        className="grid gap-1"
        style={{ gridTemplateColumns: `auto repeat(${activityDensity.hours.length}, 1fr)` }}
      >
        <div />
        {activityDensity.hours.map((h) => (
          <div key={h} className="text-center text-[9px] font-mono text-ink-dim">
            {h}
          </div>
        ))}
        {activityDensity.days.map((day, rowIdx) => (
          <Fragment key={day}>
            <div className="pr-2 text-[10px] font-mono text-ink-dim">{day}</div>
            {activityDensity.matrix[rowIdx].map((value, colIdx) => (
              <div
                key={`${day}-${colIdx}`}
                role="img"
                aria-label={`${day} ${activityDensity.hours[colIdx]}: ${Math.round(
                  value * 100
                )}% activity`}
                title={`${day} ${activityDensity.hours[colIdx]}: ${Math.round(value * 100)}%`}
                className="aspect-square rounded-sm"
                style={{ backgroundColor: `rgba(255,92,0,${0.12 + value * 0.7})` }}
              />
            ))}
          </Fragment>
        ))}
      </div>
    </div>
  );
}
