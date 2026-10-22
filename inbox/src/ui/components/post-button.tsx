"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

export interface PostButtonProps {
  url: string;
  body?: Record<string, unknown>;
  label: string;
  pendingLabel?: string;
  confirm?: string;
  variant?: "primary" | "secondary" | "danger";
}

const VARIANTS = {
  primary: "bg-blue-600 text-white hover:bg-blue-700",
  secondary: "bg-white text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50",
  danger: "bg-white text-red-700 ring-1 ring-red-300 hover:bg-red-50",
} as const;

/** Small client island: POSTs JSON, then refreshes the server-rendered page. */
export function PostButton({ url, body = {}, label, pendingLabel, confirm, variant = "secondary" }: PostButtonProps) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (confirm && !window.confirm(confirm)) return;
    setError(null);
    const response = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!response.ok) {
      const json: unknown = await response.json().catch(() => null);
      const message =
        typeof json === "object" && json !== null && "error" in json && typeof json.error === "object" && json.error !== null && "message" in json.error
          ? String(json.error.message)
          : `Request failed (${response.status})`;
      setError(message);
      return;
    }
    startTransition(() => router.refresh());
  }

  return (
    <span className="inline-flex flex-col">
      <button
        type="button"
        disabled={pending}
        onClick={() => void run()}
        className={`rounded-md px-3 py-1.5 text-sm font-medium shadow-sm disabled:opacity-50 ${VARIANTS[variant]}`}
      >
        {pending ? (pendingLabel ?? "Working...") : label}
      </button>
      {error ? <span className="mt-1 text-xs text-red-700">{error}</span> : null}
    </span>
  );
}
