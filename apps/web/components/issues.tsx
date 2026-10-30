"use client";

import { useState } from "react";
import type { Issue } from "@/lib/api";
import { cx } from "./ui";

export function IssueList({ issues, compact }: { issues: Issue[]; compact?: boolean }) {
  return (
    <ul className={cx("space-y-2", compact && "space-y-1.5")}>
      {issues.map((i, k) => (
        <li key={`${i.code}-${k}`} className="flex gap-2 text-sm leading-relaxed">
          <span
            className={cx(
              "mt-[7px] h-2 w-2 shrink-0 rounded-full",
              i.severity === "error" ? "bg-red-600" : "bg-amber-500",
            )}
            aria-hidden
          />
          <span className="min-w-0">
            <span className={i.severity === "error" ? "text-red-800" : "text-amber-900"}>{i.message}</span>
            {i.suggestions.length > 0 && (
              <span className="block text-xs text-ink-soft">建议：{i.suggestions.join("；")}</span>
            )}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** 校验结果横幅：错误红色、提醒琥珀色，可以收起 */
export function IssuesBanner({ issues }: { issues: Issue[] }) {
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity !== "error");
  const [open, setOpen] = useState(errors.length > 0);
  if (!issues.length) return null;
  const hasErrors = errors.length > 0;
  return (
    <div className={cx("rounded-xl border", hasErrors ? "border-red-200 bg-red-50" : "border-amber-200 bg-amber-50")}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center justify-between gap-3 px-4 py-2 text-left"
      >
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[15px]">
          <span className="font-semibold">行程检查</span>
          {hasErrors && <span className="font-medium text-red-700">{errors.length} 个问题必须处理</span>}
          {warnings.length > 0 && <span className="text-amber-800">{warnings.length} 条提醒</span>}
        </span>
        <span className="flex shrink-0 items-center gap-1 text-sm text-ink-soft">
          {open ? "收起" : "展开"}
          <svg className={cx("h-4 w-4 transition-transform", open && "rotate-180")} viewBox="0 0 20 20" fill="currentColor" aria-hidden>
            <path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" />
          </svg>
        </span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-black/5 px-4 py-3">
          {hasErrors && <IssueList issues={errors} />}
          {warnings.length > 0 && <IssueList issues={warnings} />}
        </div>
      )}
    </div>
  );
}
