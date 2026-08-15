import type { Metadata } from "next";
import "./globals.css";
import { RunsProvider } from "@/lib/runs/RunsContext";

export const metadata: Metadata = {
  title: "Micro SaaS Engine",
  description: "Research-validated, retention-first micro SaaS factory",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        {/* Mounted once here, above every page -- this is what makes an
            in-flight research run survive client-side navigation between
            dashboard pages (Next.js unmounts individual page components
            on route change, but never the root layout). */}
        <RunsProvider>{children}</RunsProvider>
      </body>
    </html>
  );
}
