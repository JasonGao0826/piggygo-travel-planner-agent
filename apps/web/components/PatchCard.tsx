"use client";

import type { PatchView } from "@/lib/api";
import { IssueList } from "./issues";
import { Badge, Button } from "./ui";

export type PatchBusy = "confirm" | "reject" | null;

/** 一个等待确认的改动提案：摘要、改动清单、校验问题、确认 / 撤回 */
export function PatchCard({
  patch,
  busy,
  disabled,
  onConfirm,
  onReject,
}: {
  patch: PatchView;
  busy: PatchBusy;
  disabled?: boolean;
  onConfirm: () => void;
  onReject: () => void;
}) {
  const errors = patch.issues.filter((i) => i.severity === "error").length;
  return (
    <div className="rounded-xl border border-accent-line bg-white shadow-sm">
      <div className="flex items-start justify-between gap-3 px-4 pt-3">
        <div className="min-w-0">
          <div className="text-xs font-medium text-accent">改动提案 · 基于 V{patch.base_version}</div>
          <div className="mt-0.5 text-[15px] leading-snug font-semibold">{patch.summary}</div>
        </div>
        {errors > 0 ? <Badge tone="red">有 {errors} 个错误</Badge> : <Badge tone="accent">待确认</Badge>}
      </div>
      {patch.changes.length > 0 && (
        <ul className="mt-2 space-y-1 px-4 text-sm">
          {patch.changes.map((c, i) => (
            <li key={i} className="flex gap-2">
              <span className="text-ink-faint">·</span>
              <span className="min-w-0 whitespace-pre-line">{c}</span>
            </li>
          ))}
        </ul>
      )}
      {patch.issues.length > 0 && (
        <div className="mx-4 mt-3 rounded-lg bg-canvas px-3 py-2">
          <IssueList issues={patch.issues} compact />
        </div>
      )}
      <div className="mt-3 flex items-center gap-2 border-t border-line/70 px-4 py-3">
        <Button
          variant="primary"
          className="flex-1 sm:flex-none sm:px-6"
          onClick={onConfirm}
          disabled={!patch.can_confirm || busy !== null || disabled}
          busy={busy === "confirm"}
        >
          确认
        </Button>
        <Button onClick={onReject} disabled={busy !== null || disabled} busy={busy === "reject"} className="flex-1 sm:flex-none sm:px-6">
          撤回
        </Button>
      </div>
      {!patch.can_confirm && (
        <p className="-mt-1 px-4 pb-3 text-xs text-ink-soft">这个改动没通过检查，不能确认。可以撤回，或者告诉猪猪怎么调整。</p>
      )}
    </div>
  );
}
