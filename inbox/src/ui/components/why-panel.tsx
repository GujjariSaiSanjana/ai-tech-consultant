import type { ReactNode } from "react";
import type { ClassificationRow, DraftRow } from "../../db/drafts-repo";
import { kindLabel } from "../../domain/kind";
import type { PlaybookRepo } from "../../db/playbook-repo";
import { MODE_LABEL } from "../labels";
import { Badge } from "./badge";
import { HighlightedText } from "./highlighted-text";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-t border-slate-200 pt-3">
      <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
      {children}
    </section>
  );
}

/** Right panel: everything a reviewer needs to trust (or distrust) this draft. */
export function WhyPanel({ draft, classification, playbook }: { draft: DraftRow; classification: ClassificationRow; playbook: PlaybookRepo }) {
  const cited = new Set(draft.fact_citations_json.map((c) => c.fact_id));
  const facts = draft.facts_json.filter((f) => cited.has(f.id));
  const uncited = draft.facts_json.filter((f) => !cited.has(f.id));
  const version = playbook.version(draft.playbook_version_id);
  const rules = version ? playbook.rulesFor(version).filter((r) => draft.applied_rule_ids_json.includes(r.id)) : [];
  const screenFlags = [
    classification.injection_suspected ? "Possible prompt injection" : null,
    classification.abuse ? "Abusive language" : null,
    classification.contains_pii ? "Sensitive personal data" : null,
  ].filter((x): x is string => x !== null);

  return (
    <div className="space-y-4 text-sm">
      <div>
        <h2 className="text-base font-semibold">Why this draft</h2>
        <div className="mt-2 flex flex-wrap gap-1.5">
          <Badge tone="violet">{kindLabel(classification.kind_key)}</Badge>
          <Badge tone={classification.confidence >= 0.85 ? "green" : "amber"}>{Math.round(classification.confidence * 100)}% sure</Badge>
          <Badge tone="blue">{MODE_LABEL[draft.trust_mode]}</Badge>
          {draft.retrieval_status === "needs_facts" ? <Badge tone="red">Needs facts</Badge> : null}
          {screenFlags.map((f) => (
            <Badge key={f} tone="red">
              {f}
            </Badge>
          ))}
        </div>
        <p className="mt-2 text-xs text-slate-600">Send decision: {draft.send_decision}</p>
      </div>

      {draft.review_reasons_json.length > 0 ? (
        <Section title="Check before sending">
          <ul className="list-disc space-y-1 pl-5 text-slate-700">
            {draft.review_reasons_json.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </Section>
      ) : (
        <Section title="Check before sending">
          <p className="text-slate-600">No flags. Every claim that needs a source has one.</p>
        </Section>
      )}

      {draft.guardrail_flags_json.length > 0 ? (
        <Section title="Flagged claims">
          <div className="rounded-md bg-amber-50 p-2">
            <HighlightedText text={draft.reply_text} flags={draft.guardrail_flags_json} />
          </div>
          <ul className="mt-2 space-y-1 text-xs text-amber-900">
            {draft.guardrail_flags_json.map((f) => (
              <li key={`${f.start}-${f.kind}`}>
                <strong>{f.kind}</strong>: {f.reason}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section title={`Facts used (${facts.length})`}>
        {facts.length === 0 ? (
          <p className="text-slate-600">
            {draft.retrieval_status === "needs_facts"
              ? `The Knowledge Brain had nothing for this${draft.retrieval_error ? " (it was unavailable)" : ""}. The draft avoids stating product facts.`
              : "The draft did not cite any facts."}
            {draft.gap_ticket_id ? ` An expert task was opened: ${draft.gap_ticket_id}.` : ""}
          </p>
        ) : (
          <ul className="space-y-2">
            {facts.map((f) => {
              const claim = draft.fact_citations_json.find((c) => c.fact_id === f.id)?.claim;
              return (
                <li key={f.id} className="rounded-md border border-slate-200 bg-white p-2">
                  <div className="flex items-center gap-2">
                    <Badge>{f.id}</Badge>
                    {f.url ? (
                      <a href={f.url} target="_blank" rel="noreferrer" className="font-medium text-blue-700 hover:underline">
                        {f.title}
                      </a>
                    ) : (
                      <span className="font-medium">{f.title}</span>
                    )}
                  </div>
                  <p className="mt-1 line-clamp-4 text-xs text-slate-600">{f.text}</p>
                  {claim ? <p className="mt-1 text-xs text-slate-800">Supports: &ldquo;{claim}&rdquo;</p> : null}
                </li>
              );
            })}
          </ul>
        )}
        {uncited.length > 0 ? <p className="mt-2 text-xs text-slate-500">{uncited.length} more retrieved fact(s) were not used.</p> : null}
      </Section>

      <Section title="Least sure about">
        <p className="text-slate-700">{draft.unsure_about || "Nothing noted."}</p>
        {draft.open_questions_json.length > 0 ? (
          <ul className="mt-2 list-disc pl-5 text-slate-700">
            {draft.open_questions_json.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        ) : null}
      </Section>

      <Section title="What it learned from">
        <p className="text-slate-700">
          Playbook v{draft.playbook_version_id} with {rules.length} rule(s) for this kind, and {draft.example_ids_json.length} past sent repl
          {draft.example_ids_json.length === 1 ? "y" : "ies"} as examples.
        </p>
        {rules.length > 0 ? (
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-600">
            {rules.map((r) => (
              <li key={r.id}>
                <span className="font-mono">{r.id}</span> {r.text}
              </li>
            ))}
          </ul>
        ) : null}
        {draft.model ? <p className="mt-2 text-xs text-slate-500">Drafted by {draft.model}.</p> : null}
      </Section>
    </div>
  );
}
