export const pkr = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : `PKR ${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;

export const num = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : v.toLocaleString("en-US", { maximumFractionDigits: 2 });

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthLabel(ym: string | null | undefined) {
  if (!ym) return "—";
  const [y, m] = ym.split("-");
  return MONTHS[Number(m) - 1] ? `${MONTHS[Number(m) - 1]} ${y.slice(2)}` : ym;
}

export function dateLabel(iso: string | null | undefined) {
  if (!iso) return "—";
  const d = new Date(`${iso}T00:00:00`);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
}

export const label = (key: string) => key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
