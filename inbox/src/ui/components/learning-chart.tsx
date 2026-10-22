import type { LearningPoint } from "../queries";

const W = 900;
const H = 260;
const PAD = { left: 44, right: 16, top: 16, bottom: 34 };
const PALETTE = ["#2563eb", "#9333ea", "#0891b2", "#d97706", "#16a34a", "#dc2626", "#4f46e5", "#0d9488", "#c026d3", "#65a30d"];

export function versionColor(versionId: number): string {
  return PALETTE[(versionId - 1) % PALETTE.length] ?? "#2563eb";
}

/**
 * Every human send as a dot (height = how much the person changed the draft,
 * colour = playbook version in force) plus the rolling share of drafts sent
 * unchanged. Down-and-right dots and an up-and-right line mean it is improving.
 */
export function LearningChart({ points }: { points: readonly LearningPoint[] }) {
  if (points.length === 0) return <p className="text-sm text-slate-500">No sends yet.</p>;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
  const y = (v: number) => PAD.top + (1 - v) * innerH;
  const line = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.rollingUnchanged).toFixed(1)}`).join(" ");
  const boundaries = points.filter((p, i) => i > 0 && p.versionId !== points[i - 1]?.versionId);

  return (
    <figure>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Edit size and unchanged rate over time">
        {[0, 0.25, 0.5, 0.75, 1].map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} stroke="#e4e7eb" />
            <text x={PAD.left - 8} y={y(v) + 4} textAnchor="end" fontSize="11" fill="#616e7c">
              {Math.round(v * 100)}%
            </text>
          </g>
        ))}
        {boundaries.map((p) => (
          <g key={`b-${p.index}`}>
            <line x1={x(p.index)} x2={x(p.index)} y1={PAD.top} y2={H - PAD.bottom} stroke={versionColor(p.versionId)} strokeDasharray="3 3" />
            <text x={x(p.index) + 3} y={PAD.top + 10} fontSize="10" fill={versionColor(p.versionId)}>
              v{p.versionId}
            </text>
          </g>
        ))}
        {points.map((p) => (
          <circle key={p.index} cx={x(p.index)} cy={y(p.editDistance)} r={3} fill={versionColor(p.versionId)} opacity={0.75}>
            <title>{`${new Date(p.at).toLocaleDateString("en-GB")}: ${Math.round(p.editDistance * 100)}% changed (v${p.versionId})`}</title>
          </circle>
        ))}
        <path d={line} fill="none" stroke="#047857" strokeWidth={2.5} />
        <text x={PAD.left} y={H - 10} fontSize="11" fill="#616e7c">
          oldest send
        </text>
        <text x={W - PAD.right} y={H - 10} fontSize="11" fill="#616e7c" textAnchor="end">
          newest send
        </text>
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-4 text-xs text-slate-600">
        <span>
          <span className="mr-1 inline-block h-2 w-2 rounded-full bg-blue-600" /> dot = one reply: how much the person changed the draft (lower is better), colour = playbook version
        </span>
        <span>
          <span className="mr-1 inline-block h-0.5 w-4 bg-emerald-700 align-middle" /> line = share of the last 10 drafts sent unchanged (higher is better)
        </span>
      </figcaption>
    </figure>
  );
}
