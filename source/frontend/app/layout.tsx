import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "BillSense - AI Utility Bill Decoder",
  description: "Upload a Pakistani electricity bill, understand every charge, and ask questions in plain English.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
