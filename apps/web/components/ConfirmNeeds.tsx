"use client";

import { useState } from "react";
import { shortDate } from "@/lib/format";
import { Button } from "./ui";

export interface ConfirmNeed {
  id: string;
  title: string;
  due: string | null;
}

/** 待办页签顶部：订好了但还缺细节的地方（门牌号、入住时间、床型、停车、租车保险……），一键变成确认待办 */
export function ConfirmNeedsBar({
  needs,
  busy,
  onCreate,
}: {
  needs: ConfirmNeed[];
  busy: boolean;
  onCreate: () => void;
}) {
  const [open, setOpen] = useState(false);
  if (!needs.length) return null;
  return (
    <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[15px]">
          <span className="font-semibold">还有 {needs.length} 项待确认</span>
          <span className="text-amber-900">：订好了但还缺细节，最好分给具体的人去问。</span>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" className="min-h-9 px-3 text-sm" onClick={() => setOpen((v) => !v)}>
            {open ? "收起" : "看看是哪些"}
          </Button>
          <Button variant="primary" className="min-h-9 px-3 text-sm" busy={busy} onClick={onCreate}>
            生成待确认待办
          </Button>
        </div>
      </div>
      {open && (
        <ul className="mt-2 space-y-1 text-sm text-amber-950">
          {needs.map((n) => (
            <li key={n.id}>
              ☐ {n.title}
              {n.due && <span className="text-ink-faint">（{shortDate(n.due)} 前）</span>}
            </li>
          ))}
        </ul>
      )}
      <p className="mt-1 text-xs text-ink-faint">点了之后会先提出改动，在对话框里确认后才写进待办。</p>
    </div>
  );
}
