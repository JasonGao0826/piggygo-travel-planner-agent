"use client";

import type { Member } from "@/lib/api";
import { ROLE_LABEL } from "@/lib/format";
import { Avatar } from "./ui";

/**
 * "我是：Nina / Leo / Tony / 天哥"。每个人和猪猪单独对话（规格 13.7 方案 A）。
 * 上线后用邀请链接登录（locked）：只显示自己，不能切换。
 */
export function MemberSwitcher({
  members,
  value,
  onChange,
  disabled,
  locked,
}: {
  members: Member[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  locked?: boolean;
}) {
  const index = members.findIndex((m) => m.id === value);
  const current = index >= 0 ? members[index] : undefined;
  if (locked) {
    return (
      <span
        className="inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-line bg-white py-1 pr-3 pl-1.5 text-[15px] whitespace-nowrap"
        title="通过专属邀请链接登录"
      >
        {current && <Avatar name={current.name} size={26} index={index} />}
        <span className="text-ink-soft">我是：</span>
        <span className="font-medium">{current?.name ?? "…"}</span>
      </span>
    );
  }
  return (
    <label className="relative inline-flex min-h-10 items-center gap-1.5 rounded-lg border border-line bg-white py-1 pr-7 pl-1.5 text-[15px] whitespace-nowrap hover:border-accent-line">
      {current && <Avatar name={current.name} size={26} index={index} />}
      <span className="text-ink-soft">我是：</span>
      <span className="font-medium">{current?.name ?? "选择成员"}</span>
      <svg className="pointer-events-none absolute right-2 h-4 w-4 text-ink-faint" viewBox="0 0 20 20" aria-hidden>
        <path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      </svg>
      <select
        className="absolute inset-0 cursor-pointer opacity-0"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        aria-label="切换我是谁"
      >
        {members.map((m) => (
          <option key={m.id} value={m.id}>
            {m.name}
            {ROLE_LABEL[m.role] ? `（${ROLE_LABEL[m.role]}）` : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
