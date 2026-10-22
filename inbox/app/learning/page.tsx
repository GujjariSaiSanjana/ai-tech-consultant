import type { ReactNode } from "react";
import { THRESHOLDS } from "../../src/config/thresholds";
import { getServices } from "../../src/services/container";
import { Badge } from "../../src/ui/components/badge";
import { LearningChart, versionColor } from "../../src/ui/components/learning-chart";
import { PostButton } from "../../src/ui/components/post-button";
import { MODE_HELP, MODE_LABEL } from "../../src/ui/labels";
import { isSimulatedDemo, learningSeries, trustMeters, versionViews } from "../../src/ui/queries";

export const dynamic = "force-dynamic";

function pct(v: number | null): string {
  return v === null ? "-" : `${Math.round(v * 100)}%`;
}

function Card({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="text-base font-semibold">{title}</h2>
      {subtitle ? <p className="mb-3 text-sm text-slate-600">{subtitle}</p> : null}
      {children}
    </section>
  );
}

export default function LearningPage() {
  const s = getServices();
  const series = learningSeries(s);
  const versions = versionViews(s, series);
  const meters = trustMeters(s);
  const killSwitch = s.repos.settings.killSwitch();
  const events = s.repos.trust.events(12);
  const evalRuns = s.repos.evalRuns.recent(5);
  const usage = s.llmCalls.summary();
  const withRules = series.filter((p) => p.hadLearnedRules);
  const withoutRules = series.filter((p) => !p.hadLearnedRules);
  const rate = (pts: typeof series) => (pts.length === 0 ? null : pts.filter((p) => p.unchanged).length / pts.length);
  const meanEdit = (pts: typeof series) => (pts.length === 0 ? null : pts.reduce((a, p) => a + p.editDistance, 0) / pts.length);
  const cards = [
    { label: `Sent unchanged, no learned rules yet (${withoutRules.length})`, value: pct(rate(withoutRules)) },
    { label: `Sent unchanged, with learned rules (${withRules.length})`, value: pct(rate(withRules)) },
    { label: "Average change, no learned rules yet", value: pct(meanEdit(withoutRules)) },
    { label: "Average change, with learned rules", value: pct(meanEdit(withRules)) },
  ];

  return (
    <div className="space-y-4">
      {isSimulatedDemo(s) ? (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">
          <strong>Simulated history.</strong> This history was made by the demo seed: synthetic emails, a scripted reviewer and the offline fake model.
          It shows how the loop behaves; it is not evidence of real model quality. Real sends you make are added on top.
        </p>
      ) : null}

      <Card
        title="Is the agent getting better?"
        subtitle="Every time a person sends a reply, we measure how much they changed the draft. Fewer changes means the agent writes more like your team."
      >
        <div className="mb-3 grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
          {cards.map((c) => (
            <div key={c.label} className="rounded-md bg-slate-50 p-3">
              <div className="text-xs text-slate-500">{c.label}</div>
              <div className="text-xl font-semibold">{c.value}</div>
            </div>
          ))}
        </div>
        <p className="mb-3 text-xs text-slate-500">
          Drafts are compared by whether a rule learned from earlier edits was in force when they were written, so new kinds of email arriving late do not hide the effect.
        </p>
        <LearningChart points={series} />
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card
          title="Trust by kind of email"
          subtitle={`A kind sends on its own only after ${THRESHOLDS.autoMinStreak}+ drafts in a row go out unchanged and the worst-case unchanged rate over the last ${THRESHOLDS.rollingWindow} is at least ${Math.round(THRESHOLDS.autoMinWilsonLowerBound * 100)}%. Refunds, commitments and legal emails never send on their own.`}
        >
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-slate-500">
              <tr>
                <th className="py-1">Kind</th>
                <th>Mode</th>
                <th>In a row</th>
                <th>Worst-case rate</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {meters.map((m) => (
                <tr key={m.kindKey} title={m.blockers.join(" ")}>
                  <td className="py-1.5">{m.label}</td>
                  <td>
                    <Badge tone={m.mode === "auto" ? "green" : m.mode === "assist" ? "blue" : "neutral"} title={MODE_HELP[m.mode]}>
                      {MODE_LABEL[m.mode]}
                    </Badge>
                  </td>
                  <td>
                    {m.streak}/{m.needed}
                  </td>
                  <td>{pct(m.lowerBound)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card title="Safety controls" subtitle="Auto-sent replies are always listed in the inbox under Auto-sent.">
          <div className="flex flex-wrap items-center gap-3">
            <span className={`text-sm font-medium ${killSwitch ? "text-red-700" : "text-emerald-700"}`}>
              Kill switch is {killSwitch ? "ON: nothing sends without a person" : "off"}
            </span>
            <PostButton
              url="/api/settings/kill-switch"
              body={{ on: !killSwitch }}
              label={killSwitch ? "Turn kill switch off" : "Stop all auto-sending"}
              variant={killSwitch ? "secondary" : "danger"}
              {...(killSwitch ? {} : { confirm: "Stop every automatic send right now?" })}
            />
            <PostButton url="/api/quality-check" label="Re-check quality now" pendingLabel="Checking..." />
          </div>
          <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Recent mode changes</h3>
          <ul className="mt-1 space-y-1 text-sm">
            {events.length === 0 ? <li className="text-slate-500">None yet.</li> : null}
            {events.map((e) => (
              <li key={e.id}>
                <span className="text-slate-500">{new Date(e.created_at).toLocaleDateString("en-GB")}</span> {e.kind_key}:{" "}
                {MODE_LABEL[e.from_mode]} &rarr; <strong>{MODE_LABEL[e.to_mode]}</strong>. {e.reason}
              </li>
            ))}
          </ul>
          <h3 className="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Quality checks</h3>
          <ul className="mt-1 space-y-1 text-sm">
            {evalRuns.length === 0 ? <li className="text-slate-500">None yet.</li> : null}
            {evalRuns.map((r) => (
              <li key={r.id}>
                v{r.playbook_version_id}: score {r.mean_score.toFixed(3)} on {r.cases} dev cases{r.mocked ? " (mocked)" : ""}
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card
        title="Playbook versions"
        subtitle="Each real edit can teach the agent a rule. Rules are saved as a new version, which is used only if it scores at least as well as the current one on a fixed quality check. You can switch back to any earlier version in one click."
      >
        <ul className="divide-y divide-slate-100">
          {versions.map((v) => (
            <li key={v.version.id} className="py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-block h-3 w-3 rounded-full" style={{ background: versionColor(v.version.id) }} />
                <span className="font-semibold">v{v.version.id}</span>
                {v.isActive ? <Badge tone="green">Active</Badge> : null}
                {v.version.status === "rejected" ? <Badge tone="red">Failed quality check</Badge> : null}
                <span className="text-sm text-slate-600">
                  {v.rules.length} rule(s) · quality {v.version.eval_score === null ? "-" : v.version.eval_score.toFixed(3)}
                  {v.version.baseline_score !== null ? ` vs ${v.version.baseline_score.toFixed(3)} before` : ""} · {v.actions} send(s): {pct(v.unchangedRate)} unchanged, avg change {pct(v.meanEditDistance)}
                </span>
                {!v.isActive && v.version.status !== "rejected" ? (
                  <span className="ml-auto">
                    <PostButton url="/api/playbook/activate" body={{ versionId: v.version.id }} label={`Switch to v${v.version.id}`} confirm={`Make v${v.version.id} the active playbook?`} />
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-xs text-slate-500">{v.version.note}</p>
              {v.rules.length > 0 ? (
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-blue-700">Show rules</summary>
                  <ul className="mt-1 space-y-1 text-xs text-slate-700">
                    {v.rules.map((r) => (
                      <li key={r.id}>
                        <span className="font-mono">{r.id}</span> [{r.scope}] {r.text}{" "}
                        <span className="text-slate-500">
                          (helped {r.helpful}, hurt {r.harmful})
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Model usage" subtitle="Every model call is logged with tokens and cost.">
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-slate-500">
            <tr>
              <th className="py-1">Step</th>
              <th>Model</th>
              <th>Calls</th>
              <th>Errors</th>
              <th>Cost (USD)</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {usage.map((u) => (
              <tr key={`${u.purpose}-${u.model}`}>
                <td className="py-1">{u.purpose}</td>
                <td>
                  {u.model}
                  {u.mocked ? " (mock)" : ""}
                </td>
                <td>{u.calls}</td>
                <td>{u.errors}</td>
                <td>{u.cost_usd.toFixed(4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
