import { MODE_HELP, MODE_LABEL } from "../labels";
import type { TrustMeter } from "../queries";

const MODE_STYLE = {
  shadow: "bg-slate-100 text-slate-700",
  assist: "bg-blue-50 text-blue-800",
  auto: "bg-emerald-50 text-emerald-800",
} as const;

/** Top-bar meter: one chip per kind of email, most-seen first. */
export function TrustBar({ meters, killSwitch }: { meters: TrustMeter[]; killSwitch: boolean }) {
  if (meters.length === 0) {
    return <p className="text-xs text-slate-500">No replies sent yet. Trust is earned per kind of email as drafts go out unchanged.</p>;
  }
  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {killSwitch ? (
        <span className="shrink-0 rounded-md bg-red-100 px-2 py-1 text-xs font-semibold text-red-800">Kill switch on: nothing sends automatically</span>
      ) : null}
      {meters.slice(0, 6).map((m) => {
        const pct = Math.min(100, Math.round((m.streak / m.needed) * 100));
        return (
          <div
            key={m.kindKey}
            title={`${MODE_HELP[m.mode]}\n${m.lastReason}${m.blockers.length ? `\nTo send on its own: ${m.blockers.join(" ")}` : ""}`}
            className={`shrink-0 rounded-md px-2 py-1 text-xs ${MODE_STYLE[m.mode]}`}
          >
            <div className="font-medium">
              {m.label}: {Math.min(m.streak, m.needed)}/{m.needed} unchanged, {MODE_LABEL[m.mode].toLowerCase()}
            </div>
            <div className="mt-1 h-1 w-full rounded bg-white/70">
              <div className="h-1 rounded bg-current opacity-60" style={{ width: `${pct}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
