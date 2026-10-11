"use client";

import { useState } from "react";
import { askQuestions } from "../lib/api";
import type { Answer } from "../lib/types";

const SUGGESTIONS = ["How much of my bill is taxes?", "Why is my bill higher or lower?", "Do I have arrears or credit?", "How can I avoid the late fee?"];

export default function AskPanel({ token }: { token: string }) {
  const [text, setText] = useState("");
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ask = async () => {
    const question = text.trim();
    if (!question || busy) return;
    setBusy(true); setError(null);
    try { const res = await askQuestions(token, [question]); setAnswers((old) => [...old, ...res.answers]); setText(""); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  return (
    <div className="card ask-card">
      <div className="ask-heading"><div><div className="section-kicker">Ask BillSense</div><h2>Questions about your bill?</h2><p className="section-copy">Answers use only information visible on your uploaded bill.</p></div><span className="ai-orb">✦</span></div>
      <div className="chips">{SUGGESTIONS.map((q) => <button key={q} className="chip" onClick={() => setText(q)}>{q}</button>)}</div>
      <div className="question-box"><textarea value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void ask(); } }} placeholder="Ask anything, e.g. Why is my bill higher this month?" /><button aria-label="Ask question" disabled={busy || !text.trim()} onClick={() => void ask()}>{busy ? <span className="button-spinner" /> : "↑"}</button></div>
      <small className="input-hint">Press Enter to ask · Shift + Enter for a new line</small>
      {error && <p className="error">{error}</p>}
      {answers.length > 0 && <div className="conversation-label">Conversation</div>}
      {answers.map((a, i) => <div key={`${a.question}-${i}`} className="qa"><p className="q"><span>You</span>{a.question}</p><div className="answer"><span className="mini-orb">✦</span><p>{a.answer}</p></div></div>)}
    </div>
  );
}
