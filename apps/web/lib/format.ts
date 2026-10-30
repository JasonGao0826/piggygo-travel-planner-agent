// 日期、角色等显示用的小工具。

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function parseDate(s: string | null | undefined): Date | null {
  if (!s) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

/** 2026-11-23 → 11/23 周一 */
export function shortDate(s: string | null | undefined, withWeekday = false): string {
  const d = parseDate(s);
  if (!d) return s || "";
  const base = `${d.getMonth() + 1}/${d.getDate()}`;
  return withWeekday ? `${base} ${WEEKDAYS[d.getDay()]}` : base;
}

/** 行程日期范围，例如 "2026 年 11/23 – 12/2 · 10 天" */
export function dateRange(start: string | null | undefined, end: string | null | undefined): string {
  const a = parseDate(start);
  const b = parseDate(end);
  if (!a && !b) return "日期待定";
  if (a && b) {
    const days = Math.round((b.getTime() - a.getTime()) / 86400000) + 1;
    return `${a.getFullYear()} 年 ${shortDate(start)} – ${shortDate(end)} · ${days} 天`;
  }
  return `${(a || b)!.getFullYear()} 年 ${shortDate(start || end)}`;
}

export const ROLE_LABEL: Record<string, string> = {
  owner: "发起人",
  editor: "编辑者",
  member: "成员",
};

export const DESTINATION_LABEL: Record<string, string> = {
  NZ: "新西兰",
  AU: "澳大利亚",
};

/** 头像浅色：同一行程里按成员顺序取色（不会撞色），没有顺序时按名字算 */
const AVATAR_COLORS = [
  ["#dcebfa", "#1e4e79"],
  ["#e2f3e0", "#2f5d2b"],
  ["#fff1d2", "#7a5410"],
  ["#fce4ec", "#8a2846"],
  ["#ede7f6", "#4b2d83"],
  ["#e0f2f1", "#11554f"],
];

export function avatarColors(name: string, index?: number): { bg: string; fg: string } {
  let h = index ?? 0;
  if (index === undefined) for (const ch of name) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  const [bg, fg] = AVATAR_COLORS[h % AVATAR_COLORS.length];
  return { bg, fg };
}

export function initial(name: string): string {
  const ch = Array.from(name.trim())[0] || "?";
  return ch.toUpperCase();
}
