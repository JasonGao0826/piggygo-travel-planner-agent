"use client";

import type { ActivityEvent, MemberBrief } from "@/lib/api";
import { Avatar, ErrorBox, Spinner } from "./ui";

const KIND_ICON: Record<string, string> = {
  created: "🧭",
  version: "✅",
  evidence: "📎",
  decision: "📌",
  preferences: "📝",
  profile: "🪪",
  summary: "🧩",
  poll_created: "🗳️",
  vote: "🗳️",
  poll_closed: "🏁",
};

function when(iso: string): string {
  const t = new Date(iso);
  const mins = Math.round((Date.now() - t.getTime()) / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  if (mins < 24 * 60) return `${Math.round(mins / 60)} 小时前`;
  return t.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** 动态流：谁做了什么（设计规格 13.6 节），最新的在上面 */
export function ActivityFeed({
  events,
  members,
  error,
  onRetry,
}: {
  events: ActivityEvent[] | null;
  members: MemberBrief[];
  error: string | null;
  onRetry: () => void;
}) {
  if (!events) {
    return error ? (
      <ErrorBox onRetry={onRetry}>{error}</ErrorBox>
    ) : (
      <div className="flex items-center gap-2 py-10 text-ink-soft">
        <Spinner /> 正在读取动态…
      </div>
    );
  }
  if (!events.length) {
    return <p className="rounded-xl border border-dashed border-line bg-white px-4 py-6 text-center text-ink-soft">还没有动态。</p>;
  }
  const index = Object.fromEntries(members.map((m, i) => [m.id, { name: m.name, i }]));
  return (
    <ol className="space-y-1 rounded-xl border border-line bg-white p-2 sm:p-3">
      {events.map((e) => {
        const who = e.actor ? index[e.actor] : undefined;
        return (
          <li key={e.id} className="flex gap-3 rounded-lg px-2 py-2 hover:bg-canvas/60">
            {who ? (
              <Avatar name={who.name} index={who.i} size={30} />
            ) : (
              <span className="inline-flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-canvas text-base" aria-hidden>
                {KIND_ICON[e.kind] ?? "•"}
              </span>
            )}
            <div className="min-w-0 text-[15px] leading-relaxed">
              <div>{e.text}</div>
              <div className="text-xs text-ink-faint">{when(e.at)}</div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
