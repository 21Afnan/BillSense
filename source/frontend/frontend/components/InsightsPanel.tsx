"use client";

import { monthLabel, num, pkr } from "../lib/format";
import type { ExtractResponse } from "../lib/types";

type Card = { title: string; value: string; note?: string; tone?: "good" | "bad" };

const n = (v: unknown) => (typeof v === "number" ? v : null);
const signed = (v: number) => `${v > 0 ? "+" : ""}${num(v)}`;

function buildCards(data: ExtractResponse): Card[] {
  const m = data.insights;
  const b = data.level1;
  const cards: Card[] = [];

  const taxPct = n(m.tax_share_of_current_bill_pct);
  if (taxPct !== null) cards.push({ title: "Taxes share of bill", value: `${taxPct}%`, note: pkr(n(m.tax_total_used)) });

  const cpu = n(m.cost_per_unit_current_bill);
  if (cpu !== null) cards.push({ title: "Effective cost per unit", value: pkr(cpu), note: "current bill ÷ units" });

  const late = n(m.late_payment_surcharge_amount);
  if (late !== null && late > 0)
    cards.push({ title: "Late payment penalty", value: pkr(late), note: `Pay by the due date to save it`, tone: "bad" });

  const vsAvg = n(m.current_vs_previous_average_pct);
  if (vsAvg !== null)
    cards.push({
      title: "vs your average month",
      value: `${signed(vsAvg)}%`,
      note: `avg ${num(n(m.previous_months_average_units))} units`,
      tone: vsAvg > 0 ? "bad" : "good",
    });

  const vsLastYear = n(m.change_vs_same_month_last_year_pct);
  if (vsLastYear !== null)
    cards.push({ title: "vs same month last year", value: `${signed(vsLastYear)}%`,
                 note: String(m.same_month_last_year ?? ""), tone: vsLastYear > 0 ? "bad" : "good" });

  const subsidy = n(m.subsidy_amount_saved);
  if (subsidy !== null) cards.push({ title: "Government subsidy", value: pkr(subsidy), note: "deducted from charges", tone: "good" });

  if (b.arrears !== null && b.arrears !== 0)
    cards.push({ title: b.arrears < 0 ? "Credit carried forward" : "Unpaid arrears", value: pkr(Math.abs(b.arrears)),
                 tone: b.arrears < 0 ? "good" : "bad" });

  const days = n(m.days_issue_to_due);
  if (days !== null) cards.push({ title: "Days to pay", value: `${days} days`, note: "issue date → due date" });

  return cards;
}

function UsageChart({ data }: { data: ExtractResponse }) {
  const history = data.extras.usage_history.filter((r) => r.units !== null);
  const current = data.level1.bill_month;
  const rows = [...history];
  if (current && data.level1.units_consumed !== null && !rows.some((r) => r.month === current))
    rows.push({ month: current, units: data.level1.units_consumed, bill_amount: null, payment: null });
  if (rows.length < 2) return null;

  const values = rows.map((r) => r.units as number);
  const max = Math.max(...values.map(Math.abs), 1);
  const avg = n(data.insights.previous_months_average_units);

  return (
    <div className="chart-wrap">
      <h3>Usage history (units)</h3>
      <div className="chart" role="img" aria-label="Monthly units consumed">
        {avg !== null && <div className="avg-line" style={{ bottom: `${(Math.max(avg, 0) / max) * 100}%` }}>
          <span>avg {num(avg)}</span></div>}
        {rows.map((r) => {
          const v = r.units as number;
          return (
            <div key={r.month} className="bar-col" title={`${monthLabel(r.month)}: ${v} units`}>
              <span className="bar-val">{v}</span>
              <div className={`bar ${r.month === current ? "current" : ""} ${v < 0 ? "neg" : ""}`}
                   style={{ height: `${(Math.abs(v) / max) * 100}%` }} />
              <span className="bar-label">{monthLabel(r.month)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function InsightsPanel({ data }: { data: ExtractResponse }) {
  const cards = buildCards(data);
  const facts = data.extras.other_facts ?? [];
  return (
    <div className="card insights-card">
      <div className="section-kicker">AI-assisted analysis</div>
      <h2>What stands out</h2>
      <p className="section-copy">Calculated from the figures and history printed on this bill.</p>
      <div className="insights">
        {cards.map((c) => (
          <div key={c.title} className={`insight ${c.tone ?? ""}`}>
            <span>{c.title}</span>
            <strong>{c.value}</strong>
            {c.note && <small>{c.note}</small>}
          </div>
        ))}
      </div>
      <UsageChart data={data} />
      {cards.length === 0 && <p className="muted empty-copy">This bill does not show enough data to calculate additional insights.</p>}
      {facts.length > 0 && (
        <details>
          <summary>Other notes on the bill ({facts.length})</summary>
          <ul className="facts">{facts.map((f, i) => <li key={i}><b>{f.label}:</b> {f.value}</li>)}</ul>
        </details>
      )}
    </div>
  );
}
