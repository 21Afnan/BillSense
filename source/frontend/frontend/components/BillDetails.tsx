"use client";

import { useState } from "react";
import { dateLabel, label, monthLabel, num, pkr } from "../lib/format";
import type { ExtractResponse, LineItem } from "../lib/types";

function LineTable({ title, items, total }: { title: string; items: LineItem[]; total: number | null }) {
  return (
    <div>
      <div className="table-heading"><h3>{title}</h3><span>{items.length} line{items.length === 1 ? "" : "s"}</span></div>
      <table>
        <tbody>
          {items.length === 0 && <tr><td colSpan={2} className="muted">No itemised lines printed</td></tr>}
          {items.map((it, i) => (
            <tr key={i}><td>{label(it.type)}</td><td className="num">{num(it.amount)}</td></tr>
          ))}
          <tr className="total"><td>Printed total</td><td className="num">{num(total)}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

export default function BillDetails({ data }: { data: ExtractResponse }) {
  const [showJson, setShowJson] = useState(false);
  const b = data.level1;

  const fields: [string, string, boolean?][] = [
    ["Provider", b.provider ?? "—"],
    ["Tariff", b.tariff ?? "—"],
    ["Sanctioned load", b.sanctioned_load_kw === null ? "—" : `${b.sanctioned_load_kw} kW`],
    ["Bill month", monthLabel(b.bill_month)],
    ["Reading date", dateLabel(b.reading_date)],
    ["Issue date", dateLabel(b.issue_date)],
    ["Due date", dateLabel(b.due_date), true],
    ["Units consumed", num(b.units_consumed), true],
    ["Previous reading", num(b.previous_reading)],
    ["Current reading", num(b.current_reading)],
    ["Current bill", pkr(b.current_bill), true],
    ["Arrears", pkr(b.arrears)],
    ["Payable by due date", pkr(b.payable_within_due_date), true],
    ["Payable after due date", pkr(b.payable_after_due_date)],
  ];

  return (
    <div className="card details-card">
      <div className="row-between">
        <div><div className="section-kicker">Bill summary</div><h2>Extracted details <span className="muted">{data.bill_id}</span></h2></div>
        <button className="ghost" onClick={() => setShowJson(!showJson)}>{showJson ? "Summary" : "JSON"}</button>
      </div>

      {showJson ? (
        <pre>{JSON.stringify(b, null, 2)}</pre>
      ) : (
        <>
          <div className="grid">
            {fields.map(([k, v, big]) => (
              <div key={k} className={`kv ${big ? "big" : ""}`}>
                <span>{k}</span>
                <strong className={v === "—" ? "muted" : ""}>{v}</strong>
              </div>
            ))}
          </div>
          <div className="two">
            <LineTable title="Charges" items={b.charges} total={b.total_charges} />
            <LineTable title="Taxes" items={b.taxes} total={b.total_taxes} />
          </div>
          <div className="checks">
            {data.issues.length === 0 ? (
              <p className="ok">✓ All consistency checks passed (charges, taxes and totals add up).</p>
            ) : (
              <>
                <p className="warn">⚠ Consistency notes (values are kept exactly as printed):</p>
                <ul>{data.issues.map((i) => <li key={i} className="warn">{i}</li>)}</ul>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
