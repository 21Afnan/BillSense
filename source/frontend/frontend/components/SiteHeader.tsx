import Link from "next/link";

export default function SiteHeader({ active = "" }: { active?: string }) {
  return <header className="topbar"><Link href="/" className="brand"><span className="brand-mark">⚡</span><div><strong>BillSense</strong><span>Utility intelligence</span></div></Link><nav><Link className={active === "home" ? "active" : ""} href="/">Home</Link><Link className={active === "analyze" ? "active" : ""} href="/analyze">Analyze bill</Link><Link className={active === "about" ? "active" : ""} href="/about">How it works</Link></nav><Link className="nav-cta" href="/analyze">Try BillSense →</Link></header>;
}
