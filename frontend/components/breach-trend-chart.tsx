"use client";

import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis } from "recharts";
import { breachTrend } from "@/lib/mock-data";

export function BreachTrendChart() {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={breachTrend} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
        <XAxis
          dataKey="day"
          ticks={[1, 15, 30]}
          tickFormatter={(d) => `Day ${d}`}
          tick={{ fill: "#71717A", fontSize: 10, fontFamily: "var(--font-geist-mono)" }}
          axisLine={{ stroke: "#232327" }}
          tickLine={false}
        />
        <Tooltip
          cursor={{ fill: "rgba(255,92,0,0.06)" }}
          contentStyle={{
            background: "#111113",
            border: "1px solid #232327",
            borderRadius: 6,
            fontSize: 11,
            fontFamily: "var(--font-geist-mono)",
          }}
          labelFormatter={(d) => `Day ${d}`}
        />
        <Bar dataKey="breaches" fill="#FF5C00" radius={[2, 2, 0, 0]} maxBarSize={14} />
      </BarChart>
    </ResponsiveContainer>
  );
}
