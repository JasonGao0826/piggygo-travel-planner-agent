"use client";

import { useState, type KeyboardEvent, type ReactNode } from "react";
import { Badge, cx, inputClass } from "./ui";

/** 多个短词的输入：回车或逗号添加，点 × 删除。每项最多 200 字、最多 maxItems 项（和后端一致）。 */
export function TagInput({
  value,
  onChange,
  placeholder,
  suggestions,
  maxItems = 30,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
  suggestions?: string[];
  maxItems?: number;
}) {
  const [text, setText] = useState("");

  function add(raw: string) {
    const parts = raw
      .split(/[,，、;；\n]+/)
      .map((s) => s.trim().slice(0, 200))
      .filter(Boolean)
      .filter((s) => !value.includes(s));
    if (parts.length) onChange([...value, ...parts].slice(0, maxItems));
    setText("");
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.nativeEvent.isComposing) return;
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(text);
    } else if (e.key === "Backspace" && !text && value.length) {
      onChange(value.slice(0, -1));
    }
  }

  const unused = (suggestions ?? []).filter((s) => !value.includes(s));
  return (
    <div>
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-lg border border-line bg-white px-2 py-1.5 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/20">
        {value.map((v) => (
          <span key={v} className="inline-flex items-center gap-1 rounded-full bg-accent-soft py-0.5 pr-1 pl-2.5 text-sm text-accent">
            {v}
            <button
              type="button"
              onClick={() => onChange(value.filter((x) => x !== v))}
              className="inline-flex h-6 w-6 items-center justify-center rounded-full hover:bg-white"
              aria-label={`删除 ${v}`}
            >
              ×
            </button>
          </span>
        ))}
        <input
          className="min-w-[8em] flex-1 border-0 bg-transparent px-1 py-1 outline-none placeholder:text-ink-faint"
          value={text}
          onChange={(e) => {
            const v = e.target.value;
            if (/[,，、;；]$/.test(v)) add(v);
            else setText(v);
          }}
          onKeyDown={onKeyDown}
          onBlur={() => text.trim() && add(text)}
          placeholder={value.length ? "" : placeholder}
        />
      </div>
      {unused.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {unused.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onChange([...value, s])}
              className="min-h-8 rounded-full border border-dashed border-line px-2.5 text-sm text-ink-soft hover:border-accent-line hover:text-accent"
            >
              ＋ {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** 单选按钮组（选项少的时候比下拉框好点） */
export function Choice<T extends string>({
  value,
  onChange,
  options,
  columns = 2,
}: {
  value: T | "";
  onChange: (v: T) => void;
  options: { value: T; label: string; hint?: string }[];
  columns?: 2 | 3 | 4;
}) {
  const cols = { 2: "grid-cols-2", 3: "grid-cols-3", 4: "grid-cols-2 sm:grid-cols-4" }[columns];
  return (
    <div className={cx("grid gap-2", cols)} role="radiogroup">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => onChange(o.value)}
            className={cx(
              "min-h-11 rounded-lg border px-3 py-2 text-left text-[15px] transition-colors",
              on ? "border-accent bg-accent-soft font-medium text-accent" : "border-line bg-white hover:border-accent-line",
            )}
          >
            {o.label}
            {o.hint && <span className="block text-xs font-normal text-ink-faint">{o.hint}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** 多选小标签 */
export function MultiChips({
  value,
  onChange,
  options,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  options: string[];
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => {
        const on = value.includes(o);
        return (
          <button
            key={o}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((x) => x !== o) : [...value, o])}
            className={cx(
              "min-h-10 rounded-full border px-3.5 text-[15px]",
              on ? "border-accent bg-accent-soft font-medium text-accent" : "border-line bg-white hover:border-accent-line",
            )}
          >
            {on ? "✓ " : ""}
            {o}
          </button>
        );
      })}
    </div>
  );
}

export function Section({
  title,
  hint,
  action,
  children,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4 rounded-xl border border-line bg-white p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-[17px] font-semibold">{title}</h2>
          {hint && <p className="mt-0.5 text-sm text-ink-soft">{hint}</p>}
        </div>
        {action && <div className="-mt-1 -mr-2 shrink-0">{action}</div>}
      </div>
      {children}
    </section>
  );
}

/**
 * 折叠起来的选填项。summary 写里面有什么、为什么值得填（折叠时也看得到）；
 * filled 是已经填了几项，折叠着也知道里面有内容。
 */
export function Collapsible({
  title,
  summary,
  filled,
  children,
  defaultOpen,
}: {
  title: string;
  summary?: ReactNode;
  filled?: number;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="group rounded-lg border border-line bg-canvas/50" open={defaultOpen}>
      <summary className="flex min-h-11 cursor-pointer list-none flex-col justify-center px-3 py-2.5">
        <span className="flex items-center justify-between gap-3">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[15px] font-medium text-ink">
            {title}
            {!!filled && <Badge tone="accent">已填 {filled} 项</Badge>}
          </span>
          <span className="flex shrink-0 items-center gap-1 text-sm text-accent" aria-hidden>
            <span className="group-open:hidden">展开</span>
            <span className="hidden group-open:inline">收起</span>
            <svg className="h-4 w-4 transition-transform group-open:rotate-180" viewBox="0 0 20 20" fill="currentColor" aria-hidden>
              <path d="M5.5 7.5 10 12l4.5-4.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" />
            </svg>
          </span>
        </span>
        {summary && <span className="mt-0.5 block text-sm text-ink-soft">{summary}</span>}
      </summary>
      <div className="space-y-4 border-t border-line px-3 py-4">{children}</div>
    </details>
  );
}

export { inputClass };
