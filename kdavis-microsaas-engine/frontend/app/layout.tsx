import type { Metadata } from "next";
import "./globals.css";
import { UsageTracker } from "@/components/UsageTracker";

export const metadata: Metadata = {
  title: "Micro SaaS Engine",
  description: "Research-validated, retention-first micro SaaS factory",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-[#0a0a0a] text-[#ededed]">
        <UsageTracker eventType="page_view" />
        {children}
      </body>
    </html>
  );
}
