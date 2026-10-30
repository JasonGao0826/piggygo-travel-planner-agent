"use client";

import { useRef, useState } from "react";
import { api, errorText, type Guide } from "@/lib/api";
import { Badge, Button, ErrorBox, Spinner, cx, inputClass } from "./ui";

/** 一篇攻略读出来的内容：地点候选可以一键加进想去或必玩 */
export function GuideCard({
  guide,
  onWish,
  onMust,
  onDelete,
  compact,
}: {
  guide: Guide;
  onWish?: (name: string) => void;
  onMust?: (name: string, hint: string | null) => void;
  onDelete?: () => void;
  compact?: boolean;
}) {
  const x = guide.extraction;
  const [added, setAdded] = useState<Record<string, string>>({});
  return (
    <div className="space-y-2 rounded-lg border border-line bg-white p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium">
            {guide.url ? (
              <a href={guide.url} target="_blank" rel="noopener noreferrer" className="text-accent underline underline-offset-2">
                {guide.title}
              </a>
            ) : (
              guide.title
            )}
          </div>
          <div className="text-xs text-ink-faint">
            {guide.added_by_name} 贴的 · {guide.source}
          </div>
        </div>
        {onDelete && (
          <Button variant="ghost" className="min-h-8 px-2 text-xs" onClick={onDelete}>
            删除
          </Button>
        )}
      </div>
      <p className="text-sm text-ink">{guide.summary}</p>
      {!compact && x.places.length > 0 && (
        <ul className="space-y-1.5">
          {x.places.map((p, i) => (
            <li key={i} className="rounded-lg bg-canvas px-3 py-2 text-sm">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="font-medium">{p.name}</span>
                {p.place_name ? <Badge tone="green">知识库：{p.place_name}</Badge> : <Badge>知识库里没有，要先核实</Badge>}
              </div>
              {p.what && <div className="text-ink-soft">{p.what}</div>}
              {p.tips.length > 0 && <div className="text-xs text-ink-faint">{p.tips.join("；")}</div>}
              {(onWish || onMust) && (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {added[p.name] ? (
                    <span className="text-xs text-emerald-700">已加进{added[p.name]}，记得保存</span>
                  ) : (
                    <>
                      {onWish && (
                        <Button className="min-h-8 px-2.5 text-xs" onClick={() => (onWish(p.place_name ?? p.name), setAdded({ ...added, [p.name]: "想去" }))}>
                          加进想去
                        </Button>
                      )}
                      {onMust && (
                        <Button
                          className="min-h-8 px-2.5 text-xs"
                          onClick={() => (onMust(p.place_name ?? p.name, p.place_name), setAdded({ ...added, [p.name]: "必玩" }))}
                        >
                          加进必玩
                        </Button>
                      )}
                    </>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {!compact &&
        [
          ["价格（攻略里说的，可能过时）", x.prices],
          ["预约建议", x.booking_tips],
          ["注意事项", x.cautions],
        ].map(([label, rows]) =>
          (rows as string[]).length ? (
            <div key={label as string} className="text-sm">
              <span className="text-ink-soft">{label as string}：</span>
              {(rows as string[]).join("；")}
            </div>
          ) : null,
        )}
      {!compact && <p className="text-xs text-ink-faint">攻略只当参考，价格和营业时间以官网为准。</p>}
    </div>
  );
}

/** 读小红书攻略：贴链接或整段分享文字；读不到就上传笔记截图 */
export function GuideReader({
  tripId,
  memberId,
  onRead,
  onWish,
  onMust,
}: {
  tripId: string;
  memberId: string;
  onRead: (g: Guide) => void;
  onWish: (name: string) => void;
  onMust: (name: string, hint: string | null) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Guide | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function read(input: { text?: string; file?: File }) {
    setBusy(true);
    setError(null);
    try {
      const g = await api.readGuide(tripId, memberId, input);
      setResult(g);
      setText("");
      onRead(g);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-lg border border-piggy bg-piggy-soft/60 p-3">
      <div className="text-sm font-medium">读小红书攻略</div>
      <textarea
        className={cx(inputClass, "min-h-16")}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="把小红书的分享链接或整段分享文字贴在这里"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" busy={busy} disabled={busy || !text.trim()} onClick={() => read({ text })}>
          {busy ? "猪猪正在读…" : "读取"}
        </Button>
        <Button disabled={busy} onClick={() => fileRef.current?.click()}>
          上传笔记截图
        </Button>
        {busy && (
          <span className="inline-flex items-center gap-1 text-xs text-ink-soft">
            <Spinner className="h-4 w-4" /> 通常要 10–30 秒
          </span>
        )}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) read({ file: f });
        }}
      />
      {error && (
        <ErrorBox>
          {error}
          {!error.includes("截图") && " 读不到的话，可以上传笔记截图。"}
        </ErrorBox>
      )}
      {result && <GuideCard guide={result} onWish={onWish} onMust={onMust} />}
      <p className="text-xs text-ink-faint">只读你贴的这一篇，只保存标题、链接和要点。读到的地点可以一键加进想去或必玩，保存偏好后大家都能看到。</p>
    </div>
  );
}
