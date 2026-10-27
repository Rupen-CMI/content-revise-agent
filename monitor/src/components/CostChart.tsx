"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { axisCost, shortDay, type ChartPoint } from "@/lib/chart";
import { fmtMoney, type Currency } from "@/lib/currency";

// Colours come from the theme tokens through CSS (see ".cost-chart" in globals.css), so the chart
// follows light/dark without any JavaScript. `data` is already converted to `currency`.

function Tip({ active, payload, currency }: { active?: boolean; payload?: ReadonlyArray<{ payload: ChartPoint }>; currency: Currency }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="chart-tip">
      <div className="chart-tip-day">{shortDay(p.day)}</div>
      <div className="chart-tip-row">
        <span>Cost</span>
        <strong>{fmtMoney(p.cost, currency)}</strong>
      </div>
      <div className="chart-tip-row">
        <span>Runs</span>
        <strong>{p.runs}</strong>
      </div>
    </div>
  );
}

export function CostChart({ data, currency = "USD" }: { data: ChartPoint[]; currency?: Currency }) {
  const total = data.reduce((s, d) => s + d.cost, 0);
  if (total <= 0) {
    return <p className="muted">No completed runs in the last 30 days yet.</p>;
  }
  return (
    <div className="chart cost-chart" role="img" aria-label="Daily LLM cost over the last 30 days">
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="costFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" className="grad-top" />
              <stop offset="100%" className="grad-bottom" />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} strokeDasharray="3 4" />
          <XAxis
            dataKey="day"
            tickFormatter={shortDay}
            tickLine={false}
            axisLine={false}
            interval="preserveStartEnd"
            minTickGap={36}
            tickMargin={8}
          />
          <YAxis
            width={48}
            tickFormatter={(v: number) => axisCost(v, currency)}
            tickLine={false}
            axisLine={false}
            allowDecimals
            domain={[0, "auto"]}
            tickCount={4}
          />
          <Tooltip content={<Tip currency={currency} />} cursor={{ className: "chart-cursor" }} isAnimationActive={false} />
          <Area
            type="monotone"
            dataKey="cost"
            className="cost-area"
            fill="url(#costFill)"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, className: "cost-dot" }}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
