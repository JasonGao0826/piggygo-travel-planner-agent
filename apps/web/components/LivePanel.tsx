"use client";

import { useState } from "react";
import type { DayForecast, Issue, LiveReport } from "@/lib/api";
import { Button, Spinner, cx } from "./ui";

const SOURCE_LABEL: Record<string, string> = { roads: "路况", alerts: "天气预警", forecast: "天气预报" };

function mdDate(iso: string): string {
  const [, m, d] = iso.split("-");
  return `${Number(m)}/${Number(d)}`;
}

function hm(iso: string): string {
  const d = new Date(iso);
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function ForecastRow({ f }: { f: DayForecast }) {
  const parts = [
    f.tmin != null && f.tmax != null ? `${Math.round(f.tmin)}–${Math.round(f.tmax)}°C` : null,
    f.precip_mm != null ? `雨 ${f.precip_mm} 毫米${f.precip_prob != null ? `（${f.precip_prob}%）` : ""}` : null,
    f.gust_kmh != null ? `阵风 ${Math.round(f.gust_kmh)} km/h` : null,
  ].filter(Boolean);
  return (
    <tr className="border-b border-line/70 last:border-b-0">
      <td className="px-3 py-1.5 align-top whitespace-nowrap">{mdDate(f.date)}</td>
      <td className="px-3 py-1.5 align-top">
        {f.places.slice(0, 3).join("、")}
        {f.places.length > 3 && ` 等 ${f.places.length} 处`}
      </td>
      <td className="px-3 py-1.5 align-top whitespace-nowrap">{f.weather}</td>
      <td className="px-3 py-1.5 align-top text-ink-soft">{parts.join(" · ")}</td>
    </tr>
  );
}

/** 实时情况：路况、天气预警、天气预报、公众假日。每条提醒都可以一键交给猪猪调整。 */
export function LivePanel({
  live,
  loading,
  error,
  onRetry,
  onAsk,
  disabled,
}: {
  live: LiveReport | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onAsk: (issue: Issue) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const issues = live?.issues ?? [];
  const days = live?.days ?? [];
  if (!live && !loading && !error) return null;

  const times = Object.entries(live?.fetched_at ?? {}).map(([k, v]) => `${SOURCE_LABEL[k] ?? k} ${hm(v)}`);

  return (
    <div className={cx("rounded-xl border", issues.length ? "border-sky-200 bg-sky-50" : "border-line bg-white")}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex min-h-11 w-full items-center justify-between gap-3 px-4 py-2 text-left"
      >
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[15px]">
          <span className="font-semibold">实时情况</span>
          {loading && !live && (
            <span className="inline-flex items-center gap-1 text-sm text-ink-soft">
              <Spinner /> 正在查路况和天气…
            </span>
          )}
          {live && (
            <span className={issues.length ? "text-sky-800" : "text-ink-soft"}>
              {issues.length ? `${issues.length} 条和行程有关` : "没有发现影响行程的情况"}
            </span>
          )}
          {error && <span className="text-red-700">没查到</span>}
        </span>
        <span className="shrink-0 text-sm text-ink-soft">{open ? "收起" : "展开"}</span>
      </button>

      {open && (
        <div className="space-y-3 border-t border-black/5 px-4 py-3 text-sm leading-relaxed">
          {error && (
            <p className="text-red-700">
              {error}{" "}
              <button type="button" className="underline underline-offset-2" onClick={onRetry}>
                重试
              </button>
            </p>
          )}
          {issues.length > 0 && (
            <ul className="space-y-2.5">
              {issues.map((i, k) => (
                <li key={`${i.code}-${k}`} className="rounded-lg border border-sky-200 bg-white px-3 py-2">
                  <div className="text-ink">{i.message}</div>
                  {i.suggestions.length > 0 && <div className="mt-0.5 text-xs text-ink-soft">建议：{i.suggestions.join("；")}</div>}
                  <Button className="mt-2 min-h-9 px-3 text-sm" onClick={() => onAsk(i)} disabled={disabled}>
                    让猪猪调整
                  </Button>
                </li>
              ))}
            </ul>
          )}

          {days.length > 0 && (
            <div className="scroll-thin overflow-x-auto rounded-lg border border-line bg-white">
              <table className="w-full min-w-[520px] border-collapse">
                <thead>
                  <tr className="bg-gray-50 text-left text-ink-soft">
                    <th className="px-3 py-1.5 font-semibold">日期</th>
                    <th className="px-3 py-1.5 font-semibold">地点</th>
                    <th className="px-3 py-1.5 font-semibold">天气（最差）</th>
                    <th className="px-3 py-1.5 font-semibold">气温 · 降雨 · 风</th>
                  </tr>
                </thead>
                <tbody>
                  {days.map((f) => (
                    <ForecastRow key={f.date} f={f} />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {live && !live.enabled && <p className="text-ink-soft">后端没有开启实时数据，只检查了公众假日。</p>}
          {live?.notes.map((n, k) => (
            <p key={k} className="text-ink-soft">
              {n}
            </p>
          ))}
          {live?.errors.map((e, k) => (
            <p key={k} className="text-amber-800">
              {e}
            </p>
          ))}
          {times.length > 0 && (
            <p className="text-xs text-ink-faint">
              数据时间：{times.join("，")}。来源：新西兰交通局 NZTA、MetService（CC BY 4.0）、Open-Meteo。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
