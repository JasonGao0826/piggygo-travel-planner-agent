"use client";

import { useState } from "react";
import type { Report, ReportBlock, ReportSection } from "@/lib/api";
import { plainTitle } from "@/lib/tabs";
import { cx } from "./ui";

const hex = (fill: string | null | undefined) => (fill ? (fill.startsWith("#") ? fill : `#${fill}`) : undefined);

// 状态单元格用统一的颜色（规格 17.3：状态用统一图标）
const STATUS_TONE: Record<string, string> = {
  已订: "bg-emerald-50 text-emerald-700 border-emerald-200",
  "☑ 已完成": "bg-emerald-50 text-emerald-700 border-emerald-200",
  待订: "bg-amber-50 text-amber-800 border-amber-200",
  "☐ 待完成": "bg-amber-50 text-amber-800 border-amber-200",
  待核实: "bg-yellow-50 text-yellow-800 border-yellow-200",
  无需预约: "bg-gray-50 text-ink-soft border-line",
};

const URL_RE = /https?:\/\/[^\s｜，。、）)]+/g;

/** 长链接只显示网站名（例如 airbnb.com），点开是完整地址。和 Word 报告的做法一致。 */
function linkLabel(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function Linkified({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(URL_RE)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(text.slice(last, at));
    parts.push(
      <a key={at} href={m[0]} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
        {linkLabel(m[0])}
      </a>,
    );
    last = at + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return <>{parts}</>;
}

function Cell({ text }: { text: string }) {
  const tone = STATUS_TONE[text.trim()];
  if (tone) {
    return (
      <span className={cx("inline-block rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap", tone)}>
        {text.trim()}
      </span>
    );
  }
  return <Linkified text={text} />;
}

export function ReportTable({ block }: { block: Extract<ReportBlock, { kind: "table" }> }) {
  const widths = block.widths_mm && block.widths_mm.length === block.columns.length ? block.widths_mm : null;
  const total = widths ? widths.reduce((a, b) => a + b, 0) : 0;
  // Word 里是横向 A4；网页按比例给最小宽度，手机上在容器里横向滚动
  const minWidth = widths ? Math.max(Math.round(total * 2.4), 520) : 520;
  return (
    <div className="scroll-thin overflow-x-auto rounded-lg border border-line bg-white">
      <table className="w-full border-collapse text-sm leading-relaxed" style={{ minWidth }}>
        {widths && (
          <colgroup>
            {widths.map((w, i) => (
              <col key={i} style={{ width: `${((w / total) * 100).toFixed(2)}%` }} />
            ))}
          </colgroup>
        )}
        <thead>
          <tr className="bg-gray-50">
            {block.columns.map((c, i) => (
              <th key={i} className="border-b border-line px-3 py-2 text-left font-semibold whitespace-nowrap text-ink-soft">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((r, i) => (
            <tr
              key={i}
              className={cx(r.bold && "font-semibold", "border-b border-line/70 last:border-b-0")}
              style={{ background: hex(r.fill) }}
            >
              {r.cells.map((c, j) => (
                <td key={j} className="px-3 py-2 align-top whitespace-pre-line">
                  <Cell text={c} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ReportCard({ block }: { block: Extract<ReportBlock, { kind: "card" }> }) {
  const [en, zh] = block.title.includes("｜") ? block.title.split("｜", 2) : [null, block.title];
  return (
    <article className="flex flex-col overflow-hidden rounded-xl border border-line bg-white">
      <div className="px-4 py-3" style={{ background: hex(block.fill) || "#f6f7f9" }}>
        {en && <div className="text-xs text-ink-soft">{en}</div>}
        <h3 className="text-[16px] leading-snug font-semibold">{zh}</h3>
        {block.intro && <p className="mt-1 text-sm text-ink-soft">{block.intro}</p>}
      </div>
      <dl className="flex-1 divide-y divide-line/70 px-4">
        {block.facts.map(([label, value], i) => (
          <div key={i} className="grid grid-cols-[4.8em_minmax(0,1fr)] gap-3 py-2 text-sm">
            <dt className="text-ink-faint">{label}</dt>
            <dd className="whitespace-pre-line">{value}</dd>
          </div>
        ))}
      </dl>
      {block.links.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-line/70 px-4 py-3">
          {block.links.map((l, i) => (
            <a
              key={i}
              href={l.url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-9 items-center rounded-lg border border-accent-line bg-accent-soft px-3 text-sm font-medium text-accent hover:bg-accent-soft/70"
            >
              {l.label} ↗
            </a>
          ))}
        </div>
      )}
    </article>
  );
}

export function ReportHeading({ block, id }: { block: Extract<ReportBlock, { kind: "heading" }>; id?: string }) {
  if (block.fill) {
    return (
      <h3
        id={id}
        className="scroll-mt-40 rounded-lg px-3 py-2 text-[15px] font-semibold"
        style={{ background: hex(block.fill) }}
      >
        {block.text}
      </h3>
    );
  }
  return (
    <h3 id={id} className="scroll-mt-40 pt-2 text-[16px] font-semibold text-ink">
      {block.text}
    </h3>
  );
}

const FOLD_AFTER = 6;

/** 长清单（例如假设、本版变更）先显示前几条，其余点开再看 */
function BulletList({ items }: { items: string[] }) {
  const [open, setOpen] = useState(false);
  const foldable = items.length > FOLD_AFTER + 2;
  const shown = foldable && !open ? items.slice(0, FOLD_AFTER) : items;
  return (
    <div>
      <ul className="list-disc space-y-1 pl-5 text-[15px] marker:text-ink-faint">
        {shown.map((it, j) => (
          <li key={j} className="whitespace-pre-line">
            {it}
          </li>
        ))}
      </ul>
      {foldable && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="mt-1.5 min-h-9 pl-5 text-sm text-accent hover:underline"
          aria-expanded={open}
        >
          {open ? "收起" : `展开其余 ${items.length - FOLD_AFTER} 条`}
        </button>
      )}
    </div>
  );
}

/** 把一个章节的内容块渲染出来。连续的卡片排成网格。 */
export function SectionBlocks({ blocks, idPrefix }: { blocks: ReportBlock[]; idPrefix?: string }) {
  const out: React.ReactNode[] = [];
  let cards: Extract<ReportBlock, { kind: "card" }>[] = [];
  const flushCards = (key: string) => {
    if (cards.length) {
      out.push(
        <div key={key} className="grid gap-3 xl:grid-cols-2">
          {cards.map((c, i) => (
            <ReportCard key={i} block={c} />
          ))}
        </div>,
      );
      cards = [];
    }
  };
  blocks.forEach((b, i) => {
    if (b.kind === "card") {
      cards.push(b);
      return;
    }
    flushCards(`cards-${i}`);
    switch (b.kind) {
      case "paragraph":
        out.push(
          <p key={i} className={b.muted ? "text-sm text-ink-faint" : "text-[15px]"}>
            {b.text}
          </p>,
        );
        break;
      case "bullets":
        out.push(<BulletList key={i} items={b.items} />);
        break;
      case "table":
        out.push(<ReportTable key={i} block={b} />);
        break;
      case "heading":
        out.push(<ReportHeading key={i} block={b} id={idPrefix ? `${idPrefix}-${i}` : undefined} />);
        break;
    }
  });
  flushCards("cards-end");
  return <div className="space-y-3">{out}</div>;
}

export function ReportSectionView({ section, idPrefix }: { section: ReportSection; idPrefix?: string }) {
  return (
    <section className="space-y-3">
      <h2 className="text-[17px] font-semibold">{plainTitle(section.title)}</h2>
      <SectionBlocks blocks={section.blocks} idPrefix={idPrefix} />
    </section>
  );
}

export function Legend({ legend }: { legend: Report["legend"] }) {
  if (!legend.length) return null;
  return (
    <div className="flex flex-wrap gap-2">
      {legend.map((l) => (
        <span key={l.city} className="inline-flex items-center gap-1.5 rounded-full border border-line bg-white px-2.5 py-1 text-sm">
          <span className="h-3 w-3 rounded-full border border-black/10" style={{ background: l.color }} />
          {l.city}
        </span>
      ))}
    </div>
  );
}

export function CoverCard({ report }: { report: Report }) {
  return (
    <div className="rounded-xl border border-line bg-white p-4 sm:p-5">
      <h2 className="text-[17px] font-semibold">{report.title}</h2>
      <ul className="mt-2 space-y-1 text-[15px] text-ink-soft">
        {report.cover_lines.map((l, i) => (
          <li key={i}>{l}</li>
        ))}
      </ul>
      {report.legend.length > 0 && (
        <div className="mt-3">
          <div className="mb-1.5 text-xs text-ink-faint">地点配色</div>
          <Legend legend={report.legend} />
        </div>
      )}
    </div>
  );
}

/** 每日页签：顶部日期导航 + 每天的标题和时间表 */
export function DailyView({ sections }: { sections: ReportSection[] }) {
  const days: { id: string; label: string; fill: string | null }[] = [];
  sections.forEach((s, si) =>
    s.blocks.forEach((b, bi) => {
      if (b.kind === "heading") {
        const head = b.text.split("｜")[0].trim().split(/\s+/).slice(0, 2).join(" ");
        days.push({ id: `day-${si}-${bi}`, label: head, fill: b.fill });
      }
    }),
  );
  return (
    <div className="space-y-4">
      {days.length > 1 && (
        <nav className="no-scrollbar -mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0">
          {days.map((d) => (
            <button
              key={d.id}
              type="button"
              onClick={() => document.getElementById(d.id)?.scrollIntoView({ behavior: "smooth", block: "start" })}
              className="min-h-9 shrink-0 rounded-full border border-black/5 px-3 text-sm whitespace-nowrap text-ink hover:border-accent-line"
              style={{ background: hex(d.fill) || "#fff" }}
            >
              {d.label}
            </button>
          ))}
        </nav>
      )}
      {sections.map((s, si) => (
        <ReportSectionView key={si} section={s} idPrefix={`day-${si}`} />
      ))}
    </div>
  );
}
