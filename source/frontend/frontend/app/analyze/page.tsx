"use client";

import { useEffect, useState } from "react";
import AskPanel from "../../components/AskPanel";
import BillDetails from "../../components/BillDetails";
import InsightsPanel from "../../components/InsightsPanel";
import UploadCard from "../../components/UploadCard";
import SiteHeader from "../../components/SiteHeader";
import { extractBill, getHealth } from "../../lib/api";
import type { ExtractResponse } from "../../lib/types";

export default function AnalyzePage() {
  const [data, setData] = useState<ExtractResponse | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null), [model, setModel] = useState<string | null>(null), [connected, setConnected] = useState(false);
  useEffect(() => { getHealth().then((h) => { setModel(h.model); setConnected(true); }).catch(() => setConnected(false)); }, []);
  const onExtract = async (file: File) => { setBusy(true); setError(null); setData(null); try { setData(await extractBill(file)); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  return <div className="app-shell"><SiteHeader active="analyze" /><div className="workspace-hero"><div><span className="eyebrow">Analysis workspace</span><h1>Decode your electricity bill</h1><p>Upload a bill and get a verified breakdown, useful insights, and bill-grounded answers.</p></div><div className={`status ${connected ? "online" : "offline"}`}><span className="status-dot" />{connected ? "AI ready" : "Backend offline"}{model && <span className="model-name">{model}</span>}</div></div><main className="workspace-main"><aside><UploadCard onExtract={onExtract} busy={busy} error={error} hasResult={Boolean(data)} /></aside><section className="results" aria-live="polite">{data ? <><div className="result-heading"><div><span className="eyebrow">Analysis complete</span><h2>Your bill, decoded</h2></div><span className="verified-pill">✓ {data.reviewed ? "AI reviewed" : "Extracted"}</span></div><BillDetails data={data} /><InsightsPanel data={data} /><AskPanel token={data.token} /></> : busy ? <div className="processing card"><div className="loader" /><h2>Reading your bill</h2><p>Extracting dates, usage, charges, taxes, and payment amounts…</p><div className="processing-steps"><span className="active">Scanning document</span><span>Checking figures</span><span>Preparing insights</span></div></div> : <div className="empty-state card"><div className="empty-illustration"><span>↑</span></div><h2>Your analysis will appear here</h2><p>Upload a bill to see a clean financial summary, month-by-month trends, and AI-powered answers.</p><div className="feature-preview"><div><b>01</b><span>Exact extraction</span></div><div><b>02</b><span>Smart insights</span></div><div><b>03</b><span>Ask anything</span></div></div></div>}</section></main><footer>BillSense · Built for WTQ 2026</footer></div>;
}
