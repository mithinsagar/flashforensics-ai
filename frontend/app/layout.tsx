import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "FlashForensics AI — agentic recovery for damaged flash storage",
  description:
    "Plug in a corrupted SD card and get your files back, with the evidence behind every call: filesystem parsing, entropy-guided carving and recoverability verdicts you can check.",
  openGraph: {
    title: "FlashForensics AI",
    description:
      "Agentic recovery for corrupted flash storage, with the evidence behind every verdict.",
    type: "website",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="grain min-h-screen antialiased">{children}</body>
    </html>
  );
}
