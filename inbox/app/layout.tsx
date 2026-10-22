import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { getServices } from "../src/services/container";
import { TrustBar } from "../src/ui/components/trust-bar";
import { trustMeters } from "../src/ui/queries";
import "./globals.css";

export const metadata: Metadata = {
  title: "Inbox Agent",
  description: "Drafts customer replies from the Knowledge Brain and learns from every send.",
};

export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: ReactNode }) {
  const services = getServices();
  const meters = trustMeters(services);
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <header className="sticky top-0 z-10 border-b border-slate-200 bg-white/95 backdrop-blur">
          <div className="mx-auto flex max-w-[1600px] items-center gap-6 px-4 py-2">
            <Link href="/inbox" className="text-sm font-semibold">
              Inbox Agent
            </Link>
            <nav className="flex gap-4 text-sm text-slate-600">
              <Link href="/inbox" className="hover:text-slate-900">
                Inbox
              </Link>
              <Link href="/learning" className="hover:text-slate-900">
                Learning
              </Link>
            </nav>
            <span className="ml-auto text-xs text-slate-500">
              {services.llm.mocked ? "Offline mode: fake model, no API key" : "Live model"} · Facts: {services.brain.name}
            </span>
          </div>
          <div className="mx-auto max-w-[1600px] px-4 pb-2">
            <TrustBar meters={meters} killSwitch={services.repos.settings.killSwitch()} />
          </div>
        </header>
        <main className="mx-auto max-w-[1600px] px-4 py-4">{children}</main>
      </body>
    </html>
  );
}
