"use client";

import type { Decision, VersionEntry } from "@/lib/api";
import { Badge } from "./ui";

const DECISION_KIND: Record<string, string> = {
  hard_constraint: "硬性要求",
  preference: "偏好",
  decision: "决定",
  rejection: "否决",
};

const PRIORITY: Record<string, string> = { must: "必须", high: "重要", normal: "一般", low: "可选" };

export function VersionsPanel({
  versions,
  current,
  decisions,
  error,
}: {
  versions: VersionEntry[] | null;
  current: number;
  decisions: Decision[];
  error: string | null;
}) {
  const list = [...(versions ?? [])].sort((a, b) => b.version - a.version);
  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h2 className="text-[17px] font-semibold">版本历史</h2>
        {error && <p className="text-sm text-red-700">{error}</p>}
        {!versions && !error && <p className="text-sm text-ink-soft">正在读取…</p>}
        <ol className="relative space-y-3 border-l-2 border-line pl-4">
          {list.map((v) => (
            <li key={v.version} className="relative">
              <span
                className={`absolute top-3 -left-[23px] h-3 w-3 rounded-full border-2 border-white ${v.version === current ? "bg-accent" : "bg-gray-300"}`}
                aria-hidden
              />
              <div className="rounded-xl border border-line bg-white px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[15px] font-semibold">V{v.version}</span>
                  {v.version === current && <Badge tone="accent">当前版本</Badge>}
                  {v.patch_id && <span className="text-xs text-ink-faint">改动 {v.patch_id}</span>}
                </div>
                {v.changelog.length > 0 && (
                  <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-sm text-ink-soft marker:text-ink-faint">
                    {v.changelog.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                )}
              </div>
            </li>
          ))}
        </ol>
      </section>

      {decisions.length > 0 && (
        <section className="space-y-3">
          <div>
            <h2 className="text-[17px] font-semibold">决策账本</h2>
            <p className="text-sm text-ink-faint">大家已经定下来的事。猪猪提改动时会遵守这些决定。</p>
          </div>
          <ul className="space-y-2">
            {decisions.map((d) => (
              <li key={d.id} className="rounded-xl border border-line bg-white px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={d.kind === "hard_constraint" ? "red" : d.kind === "rejection" ? "amber" : "neutral"}>
                    {DECISION_KIND[d.kind] ?? d.kind}
                  </Badge>
                  {d.priority && <span className="text-xs text-ink-faint">{PRIORITY[d.priority] ?? d.priority}</span>}
                  <span className="text-xs text-ink-faint">{d.id}</span>
                </div>
                <div className="mt-1 text-[15px]">{d.content}</div>
                {d.rationale && <div className="mt-0.5 text-sm text-ink-soft">{d.rationale}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
