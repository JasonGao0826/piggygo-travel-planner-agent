// 报告章节 → 网页页签。页签内容直接用报告模型渲染，保证网页和 Word 一致。
import type { Report, ReportSection } from "./api";

export type ContentTab = "overview" | "daily" | "places" | "transport" | "todo" | "vouchers" | "money" | "group" | "activity" | "versions";

export const CONTENT_TABS: { key: ContentTab; label: string }[] = [
  { key: "overview", label: "总览" },
  { key: "daily", label: "每日" },
  { key: "places", label: "项目" },
  { key: "transport", label: "住宿与交通" },
  { key: "todo", label: "待办" },
  { key: "vouchers", label: "凭证" },
  { key: "money", label: "账本" },
  { key: "group", label: "大家" },
  { key: "activity", label: "动态" },
  { key: "versions", label: "版本" },
];

/** 去掉章节编号，例如 "7. 项目详情" → "项目详情" */
export function plainTitle(title: string): string {
  return title.replace(/^\s*\d+\s*[.、．]\s*/, "");
}

// 按顺序匹配：先认"待办"类（"还没订的项目"里也有"项目"两个字），再认项目、交通。
const RULES: { tab: ContentTab; keywords: string[] }[] = [
  { tab: "vouchers", keywords: ["凭证"] },
  { tab: "money", keywords: ["费用与结算"] },
  { tab: "daily", keywords: ["每天", "每日", "时间安排", "时间表"] },
  { tab: "todo", keywords: ["待办", "还没订", "未订", "待订", "已完成的预订", "预订状态", "出发前清单"] },
  { tab: "places", keywords: ["全部游玩项目", "项目详情", "项目卡片", "景点"] },
  { tab: "transport", keywords: ["机票", "航班", "租车", "驾照", "住宿", "交通"] },
];

export function tabForSection(title: string): ContentTab {
  const t = plainTitle(title);
  for (const rule of RULES) {
    if (rule.keywords.some((k) => t.includes(k))) return rule.tab;
  }
  return "overview"; // 不认识的章节放进总览
}

export function sectionsByTab(report: Report): Record<ContentTab, ReportSection[]> {
  const out: Record<ContentTab, ReportSection[]> = {
    overview: [],
    daily: [],
    places: [],
    transport: [],
    todo: [],
    vouchers: [],
    money: [],
    group: [],
    activity: [],
    versions: [],
  };
  for (const s of report.sections) out[tabForSection(s.title)].push(s);
  // 总览页签里，行程总览表放最前面，其余按报告顺序
  out.overview.sort((a, b) => Number(plainTitle(b.title).includes("总览")) - Number(plainTitle(a.title).includes("总览")));
  return out;
}
