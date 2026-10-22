"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { z } from "zod";

const ResultSchema = z.object({
  outcome: z.enum(["unchanged", "minor_edit", "material_edit", "rewrite"]).nullable(),
  trustAfter: z.object({ mode: z.string(), streak: z.number() }).nullable(),
  playbook: z.object({ newVersionId: z.number().nullable(), promoted: z.boolean(), addedRuleIds: z.array(z.string()) }),
});

const OUTCOME_TEXT: Record<string, string> = {
  unchanged: "Sent as drafted.",
  minor_edit: "Sent with a small edit.",
  material_edit: "Sent with a real edit. The agent is learning from it.",
  rewrite: "Rewritten. The agent is learning from it.",
};

type Action = "send" | "edit_send" | "rewrite" | "reject" | "auto_rewrite";

export interface DraftEditorProps {
  draftId: number;
  draftText: string;
  status: "pending" | "sent" | "auto_sent" | "rejected";
  sentText: string | null;
  nextHref: string | null;
  prevHref: string | null;
}

function isTyping(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (target.tagName === "TEXTAREA" || target.tagName === "INPUT" || target.isContentEditable);
}

export function DraftEditor({ draftId, draftText, status, sentText, nextHref, prevHref }: DraftEditorProps) {
  const router = useRouter();
  const [text, setText] = useState(status === "pending" ? draftText : (sentText ?? draftText));
  const [rewriting, setRewriting] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const area = useRef<HTMLTextAreaElement>(null);
  const editable = status === "pending" || correcting;
  const changed = text.trim() !== draftText.trim();

  const submit = useCallback(
    async (action: Action) => {
      setError(null);
      if (action === "reject" && !window.confirm("Reject this draft? Nothing will be sent.")) return;
      const response = await fetch(`/api/drafts/${draftId}/action`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(action === "reject" ? { action } : { action, text }),
      });
      const json: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        setError("Could not save. Please try again.");
        return;
      }
      const parsed = ResultSchema.safeParse(json);
      if (parsed.success) {
        const r = parsed.data;
        const parts = [r.outcome ? OUTCOME_TEXT[r.outcome] : "Rejected."];
        if (r.playbook.newVersionId !== null) {
          parts.push(
            r.playbook.promoted
              ? `Learned ${r.playbook.addedRuleIds.length} rule(s): playbook v${r.playbook.newVersionId} is now active.`
              : `A new playbook v${r.playbook.newVersionId} was proposed but failed the quality check, so it was not used.`,
          );
        }
        if (r.trustAfter) parts.push(`This kind: ${r.trustAfter.streak} in a row unchanged, mode ${r.trustAfter.mode}.`);
        setMessage(parts.join(" "));
      }
      startTransition(() => {
        if (nextHref) router.push(nextHref);
        else router.refresh();
      });
    },
    [draftId, text, nextHref, router],
  );

  const send = useCallback(() => {
    if (correcting) return void submit("auto_rewrite");
    if (rewriting) return void submit("rewrite");
    return void submit(changed ? "edit_send" : "send");
  }, [changed, correcting, rewriting, submit]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && editable) {
        event.preventDefault();
        send();
        return;
      }
      if (isTyping(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "j" && nextHref) router.push(nextHref);
      else if (event.key === "k" && prevHref) router.push(prevHref);
      else if (status !== "pending") return;
      else if (event.key === "s") send();
      else if (event.key === "e") {
        event.preventDefault();
        area.current?.focus();
      } else if (event.key === "r") {
        event.preventDefault();
        setRewriting(true);
        setText("");
        area.current?.focus();
      } else if (event.key === "x") void submit("reject");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editable, nextHref, prevHref, router, send, status, submit]);

  return (
    <div className="flex h-full flex-col gap-3">
      <textarea
        ref={area}
        value={text}
        readOnly={!editable}
        onChange={(e) => setText(e.target.value)}
        aria-label="Reply"
        placeholder={rewriting ? "Write your own reply. The agent will learn from the difference." : undefined}
        className={`min-h-[22rem] w-full flex-1 resize-y rounded-lg border border-slate-300 p-4 font-sans text-[15px] leading-relaxed shadow-inner focus:border-blue-500 focus:outline-none ${
          editable ? "bg-white" : "bg-slate-50 text-slate-700"
        }`}
      />
      {status === "pending" ? (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={pending || text.trim() === ""}
            onClick={send}
            className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {rewriting ? "Send my rewrite" : changed ? "Edit & send" : "Send"} <kbd className="ml-1 text-slate-600">⌘↵</kbd>
          </button>
          {!rewriting ? (
            <button
              type="button"
              onClick={() => {
                setRewriting(true);
                setText("");
                area.current?.focus();
              }}
              className="rounded-md bg-white px-3 py-2 text-sm font-medium text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50"
            >
              Rewrite <kbd>r</kbd>
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                setRewriting(false);
                setText(draftText);
              }}
              className="rounded-md bg-white px-3 py-2 text-sm font-medium text-slate-800 ring-1 ring-slate-300 hover:bg-slate-50"
            >
              Back to draft
            </button>
          )}
          {changed && !rewriting ? (
            <button type="button" onClick={() => setText(draftText)} className="text-sm text-slate-600 underline">
              Undo my edits
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => void submit("reject")}
            className="ml-auto rounded-md bg-white px-3 py-2 text-sm font-medium text-red-700 ring-1 ring-red-300 hover:bg-red-50"
          >
            Reject <kbd>x</kbd>
          </button>
        </div>
      ) : null}
      {status === "auto_sent" ? (
        correcting ? (
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={pending || text.trim() === ""}
              onClick={send}
              className="rounded-md bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
            >
              Save correction and pause auto-send for this kind
            </button>
            <button type="button" onClick={() => setCorrecting(false)} className="text-sm text-slate-600 underline">
              Cancel
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setCorrecting(true)}
              className="rounded-md bg-white px-3 py-2 text-sm font-medium text-red-700 ring-1 ring-red-300 hover:bg-red-50"
            >
              This reply was wrong: correct it
            </button>
            <span className="text-xs text-slate-500">Correcting an auto-sent reply switches this kind back to review.</span>
          </div>
        )
      ) : null}
      <p className="text-xs text-slate-500">
        Shortcuts: <kbd>j</kbd>/<kbd>k</kbd> next/previous email, <kbd>e</kbd> edit, <kbd>s</kbd> send, <kbd>r</kbd> rewrite, <kbd>x</kbd> reject,{" "}
        <kbd>⌘↵</kbd> send from the editor.
      </p>
      {message ? <p className="rounded-md bg-emerald-50 p-2 text-sm text-emerald-800">{message}</p> : null}
      {error ? <p className="rounded-md bg-red-50 p-2 text-sm text-red-800">{error}</p> : null}
    </div>
  );
}
