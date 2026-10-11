export type LineItem = { type: string; amount: number };

export type Level1 = {
  provider: string | null;
  tariff: string | null;
  sanctioned_load_kw: number | null;
  bill_month: string | null;
  reading_date: string | null;
  issue_date: string | null;
  due_date: string | null;
  previous_reading: number | null;
  current_reading: number | null;
  units_consumed: number | null;
  charges: LineItem[];
  total_charges: number | null;
  taxes: LineItem[];
  total_taxes: number | null;
  current_bill: number | null;
  arrears: number | null;
  payable_within_due_date: number | null;
  payable_after_due_date: number | null;
};

export type HistoryRow = { month: string; units: number | null; bill_amount: number | null; payment: number | null };

export type Extras = {
  usage_history: HistoryRow[];
  other_facts: { label: string; value: string }[];
  [key: string]: unknown;
};

export type Insights = Record<string, unknown>;

export type ExtractResponse = {
  token: string;
  bill_id: string;
  level1: Level1;
  extras: Extras;
  issues: string[];
  reviewed: boolean;
  insights: Insights;
};

export type Answer = { question: string; answer: string };
