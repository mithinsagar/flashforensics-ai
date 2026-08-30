import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FlashForensics AI",
  description:
    "Agentic recovery for corrupted flash storage: filesystem parsing, entropy-guided carving and evidence-based recoverability verdicts.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
