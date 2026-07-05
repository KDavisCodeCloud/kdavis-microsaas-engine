import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Micro SaaS Engine",
  description: "Research-validated, retention-first micro SaaS factory",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>{children}</body>
    </html>
  );
}
