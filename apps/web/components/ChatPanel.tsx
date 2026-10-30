"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type { PatchView } from "@/lib/api";
import { Markdown } from "./Markdown";
import { PatchCard, type PatchBusy } from "./PatchCard";
import { Button, ErrorBox, Spinner, cx } from "./ui";

export type TimelineItem =
  | { kind: "message"; role: string; text: string }
  | { kind: "notice"; tone: "success" | "info" | "error"; text: string; lines?: string[] };

/** 不提具体地名、日期：每个行程都能用 */
const SUGGESTIONS = ["还有什么没订？", "哪天最累？能轻松一点吗？", "下雨的话，哪些安排要换？", "想加一顿当地特色晚餐"];

/** 系统写进对话的备注，例如"（我确认了改动：…）" */
const isNote = (text: string) => /^（[\s\S]*）$/.test(text.trim());

function Bubble({ item }: { item: TimelineItem }) {
  if (item.kind === "notice") {
    const tone = {
      success: "border-emerald-200 bg-emerald-50 text-emerald-900",
      info: "border-line bg-canvas text-ink-soft",
      error: "border-red-200 bg-red-50 text-red-800",
    }[item.tone];
    return (
      <div className={cx("rounded-lg border px-3 py-2 text-sm", tone)}>
        <div className="font-medium">{item.text}</div>
        {item.lines && item.lines.length > 0 && (
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {item.lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        )}
      </div>
    );
  }
  if (item.role === "user") {
    if (isNote(item.text)) {
      return <div className="px-2 text-center text-xs text-ink-faint">{item.text.trim().slice(1, -1)}</div>;
    }
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-accent px-3.5 py-2 text-[15px] whitespace-pre-wrap text-white">
          {item.text}
        </div>
      </div>
    );
  }
  return (
    <div className="flex items-end gap-2">
      {/* eslint-disable-next-line @next/next/no-img-element -- 静态小图 */}
      <img src="/brand/piggy.png" alt="猪猪" width={28} height={32} className="mb-0.5 h-8 w-auto shrink-0" />
      <div className="max-w-full min-w-0 rounded-2xl rounded-bl-md bg-piggy-soft px-3.5 py-2.5 text-[15px] sm:max-w-[95%]">
        <Markdown text={item.text} />
      </div>
    </div>
  );
}

function Waiting({ since, label }: { since: number; label: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.max(0, Math.round((now - since) / 1000));
  return (
    <div className="flex items-center gap-2 rounded-2xl rounded-bl-md bg-canvas px-3.5 py-2.5 text-[15px] text-ink-soft">
      <Spinner className="h-4 w-4 text-accent" />
      <span>
        {label}
        <span className="ml-1 text-xs text-ink-faint">
          {secs} 秒{secs < 45 ? "（通常要 10–40 秒）" : "，还在处理，请再等等"}
        </span>
      </span>
    </div>
  );
}

export function ChatPanel({
  memberName,
  items,
  loadingHistory,
  historyError,
  onRetryHistory,
  sending,
  onSend,
  pending,
  patchBusy,
  onConfirm,
  onReject,
  active,
}: {
  memberName: string;
  items: TimelineItem[];
  loadingHistory: boolean;
  historyError: string | null;
  onRetryHistory: () => void;
  sending: { since: number; label: string } | null;
  onSend: (text: string) => Promise<boolean>;
  pending: PatchView[];
  patchBusy: Record<string, PatchBusy>;
  onConfirm: (id: string) => void;
  onReject: (id: string) => void;
  active: boolean;
}) {
  const [text, setText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 有新消息、新提案时滚到底部
  useEffect(() => {
    const list = listRef.current;
    if (list && list.scrollHeight > list.clientHeight) list.scrollTop = list.scrollHeight;
    if (active && window.matchMedia("(max-width: 1023px)").matches) {
      endRef.current?.scrollIntoView({ block: "end" });
    }
  }, [items.length, pending.length, sending, active]);

  // 输入框随内容长高（最多约 5 行）
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const full = el.scrollHeight + el.offsetHeight - el.clientHeight; // 加上边框
    el.style.height = `${Math.min(full, 140)}px`;
    el.style.overflowY = full > 140 ? "auto" : "hidden";
  }, [text]);

  async function submit() {
    const t = text.trim();
    if (!t || sending) return;
    setText("");
    const ok = await onSend(t);
    if (!ok) setText((cur) => cur || t); // 发送失败，把原话放回输入框
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // 输入法选词时的回车不发送
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex flex-col rounded-xl border border-line bg-white lg:h-full lg:overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
        <div className="min-w-0">
          <div className="text-[15px] font-semibold">和猪猪对话</div>
          <div className="truncate text-xs text-ink-faint">以 {memberName} 的身份 · 猪猪只提出改动，确认后才生效</div>
        </div>
      </div>

      <div ref={listRef} className="scroll-thin min-h-[50vh] flex-1 space-y-3 px-3 py-3 sm:px-4 lg:min-h-0 lg:overflow-y-auto">
        {loadingHistory && (
          <div className="flex items-center gap-2 text-sm text-ink-soft">
            <Spinner className="h-4 w-4" /> 正在读取对话记录…
          </div>
        )}
        {historyError && <ErrorBox onRetry={onRetryHistory}>{historyError}</ErrorBox>}

        {!loadingHistory && !historyError && items.length === 0 && !sending && (
          <div className="rounded-xl bg-canvas px-4 py-4 text-sm text-ink-soft">
            <p>可以问问题，也可以直接说想怎么改。比如：</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => {
                    setText(s);
                    inputRef.current?.focus();
                  }}
                  className="min-h-9 rounded-full border border-line bg-white px-3 text-left text-sm text-ink hover:border-accent-line"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {items.map((item, i) => (
          <Bubble key={i} item={item} />
        ))}

        {sending && <Waiting since={sending.since} label={sending.label} />}

        {pending.length > 0 && (
          <div className="space-y-2 pt-1" id="pending-patches">
            <div className="text-sm font-semibold text-ink-soft">等待确认的改动（{pending.length}）</div>
            {pending.map((p) => (
              <PatchCard
                key={p.id}
                patch={p}
                busy={patchBusy[p.id] ?? null}
                disabled={!!sending}
                onConfirm={() => onConfirm(p.id)}
                onReject={() => onReject(p.id)}
              />
            ))}
          </div>
        )}
        <div ref={endRef} />
      </div>

      <form
        className="sticky bottom-0 border-t border-line bg-white px-3 py-2.5 pb-[max(0.625rem,env(safe-area-inset-bottom))] lg:static"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            rows={1}
            maxLength={4000}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={sending ? "猪猪正在回复…" : "问问题或提出修改，回车发送"}
            disabled={!!sending}
            className="scroll-thin max-h-36 min-h-11 flex-1 resize-none rounded-xl border border-line bg-white px-3 py-2.5 leading-normal placeholder:text-ink-faint focus:border-accent focus:ring-2 focus:ring-accent/20 focus:outline-none disabled:bg-canvas"
            aria-label="给猪猪的消息"
          />
          <Button type="submit" variant="primary" className="min-h-11 px-4" disabled={!!sending || !text.trim()}>
            发送
          </Button>
        </div>
      </form>
    </div>
  );
}
