import type { Answer, ExtractResponse } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, init);
  } catch {
    throw new Error(`Cannot reach the backend at ${API_URL}. Is it running?`);
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ?? `Request failed (${res.status})`);
  return body as T;
}

export function getHealth() {
  return request<{ status: string; level1: string; model: string; fallback_model: string | null }>("/api/health", { method: "GET" });
}

export function extractBill(file: File) {
  const form = new FormData();
  form.append("file", file);
  return request<ExtractResponse>("/api/extract", { method: "POST", body: form });
}

export function askQuestions(token: string, questions: string[]) {
  return request<{ answers: Answer[] }>("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, questions }),
  });
}
