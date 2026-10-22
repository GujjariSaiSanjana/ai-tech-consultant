import type { GuardrailFlag } from "../../domain/schemas";

/** Renders the draft with every flagged span marked, so reviewers see exactly what to check. */
export function HighlightedText({ text, flags }: { text: string; flags: readonly GuardrailFlag[] }) {
  const parts: Array<{ text: string; flag: GuardrailFlag | null }> = [];
  let cursor = 0;
  for (const flag of [...flags].sort((a, b) => a.start - b.start)) {
    if (flag.start < cursor) continue;
    parts.push({ text: text.slice(cursor, flag.start), flag: null });
    parts.push({ text: text.slice(flag.start, flag.end), flag });
    cursor = flag.end;
  }
  parts.push({ text: text.slice(cursor), flag: null });
  return (
    <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
      {parts.map((p, i) =>
        p.flag ? (
          <mark key={i} className="flag" title={p.flag.reason}>
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </p>
  );
}
