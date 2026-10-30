"use client";

import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  errorText,
  type AgentResponse,
  type BookingExtraction,
  type BookingKind,
  type EvidenceUpload,
  type Extraction,
} from "@/lib/api";
import { Button, ComingSoon, ErrorBox, Field, Spinner, inputClass } from "./ui";

const KIND_LABEL: Record<BookingKind, string> = {
  flight: "航班",
  accommodation: "住宿",
  car: "租车",
  activity: "活动",
  other: "其他",
};

type FieldType = "text" | "date" | "time" | "number" | "list";

interface FieldDef {
  key: keyof BookingExtraction & string;
  label: string | Partial<Record<BookingKind, string>> & { default: string };
  type?: FieldType;
  kinds?: BookingKind[];
  wide?: boolean;
}

const TIMED: BookingKind[] = ["flight", "car", "activity", "other"];

// 按凭证类型显示相关字段（规格 15.2），有值的字段总是显示
const FIELDS: FieldDef[] = [
  { key: "title", label: { default: "名称", flight: "航班名称", accommodation: "住宿名称", car: "租车公司 / 车型" }, wide: true },
  { key: "flight_number", label: "航班号", kinds: ["flight"] },
  { key: "from_airport", label: "出发机场", kinds: ["flight"] },
  { key: "to_airport", label: "到达机场", kinds: ["flight"] },
  { key: "date", label: { default: "日期", car: "取车日期" }, type: "date", kinds: TIMED },
  { key: "start_time", label: { default: "开始时间", flight: "起飞时间", car: "取车时间" }, type: "time", kinds: TIMED },
  { key: "end_time", label: { default: "结束时间", flight: "到达时间", car: "还车时间" }, type: "time", kinds: TIMED },
  { key: "checkin_time", label: { default: "签到时间", accommodation: "入住时间" }, type: "time", kinds: ["activity", "accommodation", "flight"] },
  { key: "check_in_date", label: "入住日期", type: "date", kinds: ["accommodation"] },
  { key: "check_out_date", label: "退房日期", type: "date", kinds: ["accommodation"] },
  { key: "location", label: { default: "地点", car: "取车地点", activity: "集合地点" }, kinds: ["accommodation", "car", "activity", "other"] },
  { key: "address", label: "地址", kinds: ["accommodation", "car", "activity", "other"], wide: true },
  { key: "pax", label: "人数", type: "number" },
  { key: "participants", label: "参加人（用顿号隔开）", type: "list" },
  { key: "confirmation_no", label: "确认号" },
  { key: "price_amount", label: "金额", type: "number" },
  { key: "price_currency", label: "币种" },
];

const labelOf = (f: FieldDef, kind: BookingKind) =>
  typeof f.label === "string" ? f.label : (f.label[kind] ?? f.label.default);

type Draft = { kind: BookingKind; values: Record<string, string>; original: BookingExtraction };

function toKind(k: unknown): BookingKind {
  return typeof k === "string" && k in KIND_LABEL ? (k as BookingKind) : "other";
}

function toDraft(b: BookingExtraction): Draft {
  const values: Record<string, string> = {};
  for (const f of FIELDS) {
    const v = b[f.key];
    if (v === null || v === undefined) values[f.key] = "";
    else if (Array.isArray(v)) values[f.key] = v.join("、");
    else values[f.key] = String(v);
  }
  return { kind: toKind(b.kind), values, original: b };
}

function fromDraft(d: Draft): BookingExtraction {
  const out: BookingExtraction = { ...d.original, kind: d.kind };
  for (const f of FIELDS) {
    const raw = (d.values[f.key] ?? "").trim();
    if (f.type === "list") {
      out[f.key] = raw ? raw.split(/[、,，;；\n]+/).map((s) => s.trim()).filter(Boolean) : [];
    } else if (f.type === "number") {
      const n = Number(raw);
      out[f.key] = raw && Number.isFinite(n) ? n : null;
    } else {
      out[f.key] = raw || null;
    }
  }
  return out;
}

function inputType(type: FieldType | undefined, value: string): string {
  // 识别结果不是标准格式时用文本框，避免日期控件把内容清空
  if (type === "date") return !value || /^\d{4}-\d{2}-\d{2}$/.test(value) ? "date" : "text";
  if (type === "time") return !value || /^\d{2}:\d{2}(:\d{2})?$/.test(value) ? "time" : "text";
  if (type === "number") return "number";
  return "text";
}

function BookingForm({
  index,
  draft,
  showAll,
  onChange,
  onRemove,
}: {
  index: number;
  draft: Draft;
  showAll: boolean;
  onChange: (d: Draft) => void;
  onRemove: () => void;
}) {
  const fields = FIELDS.filter((f) => showAll || !f.kinds || f.kinds.includes(draft.kind) || draft.values[f.key]);
  const quotes = draft.original.quotes?.filter(Boolean) ?? [];
  return (
    <fieldset className="rounded-xl border border-line bg-white p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="text-[15px] font-semibold">第 {index + 1} 条预订</div>
        <Button variant="ghost" className="min-h-9 text-sm" onClick={onRemove}>
          删除这条
        </Button>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="类型">
          <select
            className={inputClass}
            value={draft.kind}
            onChange={(e) => onChange({ ...draft, kind: e.target.value as BookingKind })}
          >
            {(Object.keys(KIND_LABEL) as BookingKind[]).map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>
        {fields.map((f) => {
          const value = draft.values[f.key] ?? "";
          return (
            <Field key={f.key} label={labelOf(f, draft.kind)} className={f.wide ? "sm:col-span-2" : undefined}>
              <input
                className={inputClass}
                type={inputType(f.type, value)}
                inputMode={f.type === "number" ? "decimal" : undefined}
                value={value}
                onChange={(e) => onChange({ ...draft, values: { ...draft.values, [f.key]: e.target.value } })}
              />
            </Field>
          );
        })}
      </div>
      {quotes.length > 0 && (
        <details className="mt-3 rounded-lg bg-canvas px-3 py-2 text-sm">
          <summary className="cursor-pointer text-ink-soft">凭证原文（{quotes.length} 处）</summary>
          <ul className="mt-2 space-y-1.5">
            {quotes.map((q, i) => (
              <li key={i} className="border-l-2 border-line pl-2 whitespace-pre-line text-ink-soft">
                {q}
              </li>
            ))}
          </ul>
        </details>
      )}
    </fieldset>
  );
}

type Phase =
  | { name: "uploading" }
  | { name: "unavailable" }
  | { name: "error"; message: string }
  | { name: "review" }
  | { name: "applying" };

/** 上传凭证 → 核对识别结果 → 应用（猪猪和行程比对后提出改动） */
export function EvidenceDialog({
  tripId,
  memberId,
  file,
  onClose,
  onApplied,
}: {
  tripId: string;
  memberId: string;
  file: File;
  onClose: () => void;
  onApplied: (res: AgentResponse, filename: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>({ name: "uploading" });
  const [upload, setUpload] = useState<EvidenceUpload | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [showAll, setShowAll] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const busy = phase.name === "uploading" || phase.name === "applying";

  // 每个文件只上传一次（开发模式下 effect 会跑两遍）
  const startedFor = useRef<File | null>(null);
  useEffect(() => {
    if (startedFor.current === file) return;
    startedFor.current = file;
    (async () => {
      try {
        const res = await api.uploadEvidence(tripId, memberId, file);
        setUpload(res);
        setDrafts((res.extraction?.bookings ?? []).map(toDraft));
        setPhase({ name: "review" });
      } catch (e) {
        if (e instanceof ApiError && e.notAvailable) setPhase({ name: "unavailable" });
        else setPhase({ name: "error", message: errorText(e) });
      }
    })();
  }, [tripId, memberId, file]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  async function apply() {
    if (!upload) return;
    setApplyError(null);
    setPhase({ name: "applying" });
    const extraction: Extraction = {
      bookings: drafts.map(fromDraft),
      notes: upload.extraction?.notes ?? [],
      unreadable: !!upload.extraction?.unreadable,
    };
    try {
      const res = await api.applyEvidence(tripId, upload.upload_id, memberId, extraction);
      onApplied(res, upload.filename || file.name);
    } catch (e) {
      setApplyError(
        e instanceof ApiError && e.notAvailable ? "应用凭证的功能即将上线，识别结果暂时不能写入行程。" : errorText(e),
      );
      setPhase({ name: "review" });
    }
  }

  const notes = upload?.extraction?.notes ?? [];

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/30 sm:items-center sm:p-6" role="dialog" aria-modal="true" aria-label="上传凭证">
      <div className="flex max-h-[92dvh] w-full flex-col rounded-t-2xl bg-canvas shadow-xl sm:max-w-2xl sm:rounded-2xl">
        <div className="flex items-center justify-between gap-3 rounded-t-2xl border-b border-line bg-white px-4 py-3">
          <div className="min-w-0">
            <div className="text-[16px] font-semibold">上传凭证</div>
            <div className="truncate text-xs text-ink-faint">{file.name}</div>
          </div>
          <Button variant="ghost" onClick={onClose} disabled={busy} aria-label="关闭">
            关闭
          </Button>
        </div>

        <div className="scroll-thin flex-1 space-y-3 overflow-y-auto px-4 py-4">
          {phase.name === "uploading" && (
            <div className="flex items-center gap-2 py-8 text-ink-soft">
              <Spinner className="h-5 w-5 text-accent" /> 正在识别凭证内容…
            </div>
          )}
          {phase.name === "unavailable" && (
            <ComingSoon title="凭证识别功能即将上线">
              上线后，上传机票、住宿、租车、活动的确认单（图片、PDF 或确认邮件 .eml），猪猪会识别出时间、确认号和金额，由你核对后和行程比对，自动提出改动。
            </ComingSoon>
          )}
          {phase.name === "error" && <ErrorBox>{phase.message}</ErrorBox>}

          {(phase.name === "review" || phase.name === "applying") && upload && (
            <>
              <p className="text-sm text-ink-soft">请核对识别出来的内容，有错直接改。点“应用”后，猪猪会和当前行程比对并提出改动，确认后才会生效。</p>
              {upload.extraction?.unreadable && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
                  这份凭证有部分内容看不清，识别结果可能不完整，请仔细核对。
                </div>
              )}
              {notes.length > 0 && (
                <ul className="list-disc space-y-1 rounded-lg bg-white px-3 py-2 pl-7 text-sm text-ink-soft">
                  {notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              )}
              {drafts.length === 0 && (
                <div className="rounded-lg border border-dashed border-line bg-white px-3 py-4 text-sm text-ink-soft">没有识别出预订信息。</div>
              )}
              {drafts.map((d, i) => (
                <BookingForm
                  key={i}
                  index={i}
                  draft={d}
                  showAll={showAll}
                  onChange={(nd) => setDrafts((all) => all.map((x, j) => (j === i ? nd : x)))}
                  onRemove={() => setDrafts((all) => all.filter((_, j) => j !== i))}
                />
              ))}
              {drafts.length > 0 && (
                <label className="flex min-h-10 items-center gap-2 text-sm text-ink-soft">
                  <input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} className="h-4 w-4 accent-accent" />
                  显示全部字段
                </label>
              )}
              {applyError && <ErrorBox>{applyError}</ErrorBox>}
            </>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-line bg-white px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:rounded-b-2xl">
          {phase.name === "applying" && (
            <span className="mr-auto flex items-center gap-2 text-sm text-ink-soft">
              <Spinner className="h-4 w-4 text-accent" /> 猪猪正在和行程比对…
            </span>
          )}
          <Button onClick={onClose} disabled={busy}>
            {phase.name === "review" ? "取消" : "关闭"}
          </Button>
          {(phase.name === "review" || phase.name === "applying") && (
            <Button variant="primary" onClick={apply} disabled={busy || drafts.length === 0} busy={phase.name === "applying"}>
              应用
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
