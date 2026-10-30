"use client";

import { useState } from "react";
import { api, errorText, type Voucher, type VoucherKind } from "@/lib/api";
import { shortDate } from "@/lib/format";
import { Section } from "./forms";
import { Badge, Button, ErrorBox, Field, Spinner, cx, inputClass } from "./ui";

const KIND_ICON: Record<string, string> = { flight: "✈️", accommodation: "🏠", car: "🚗", activity: "🎟️", other: "📄" };
const CAN_DELETE_ANY = ["owner", "editor"];

export interface ItemOption {
  id: string;
  label: string;
  date: string;
}

function size(n: number | null): string {
  if (!n) return "";
  return n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

/** 只存档：不识别、不改行程，直接放进凭证夹 */
function StoreForm({
  tripId,
  memberId,
  items,
  onDone,
  onCancel,
}: {
  tripId: string;
  memberId: string;
  items: ItemOption[];
  onDone: (v: Voucher) => void;
  onCancel: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<VoucherKind>("activity");
  const [itemId, setItemId] = useState("");
  const [date, setDate] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      onDone(await api.storeVoucher(tripId, { member_id: memberId, file, title, kind, date, item_id: itemId, notes }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border border-accent-line bg-accent-soft/40 p-3">
      <Field label="文件" hint="图片、PDF 或确认邮件，10MB 以内">
        <input
          type="file"
          accept="image/*,application/pdf,.pdf,.eml,message/rfc822"
          className={cx(inputClass, "py-1.5")}
          onChange={(e) => {
            const f = e.target.files?.[0] ?? null;
            setFile(f);
            if (f && !title) setTitle(f.name.replace(/\.[^.]+$/, "").slice(0, 120));
          }}
        />
      </Field>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="名称">
          <input className={inputClass} maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="例如 Skyline 缆车入场票" />
        </Field>
        <Field label="类型">
          <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value as VoucherKind)}>
            <option value="activity">活动、门票</option>
            <option value="flight">机票</option>
            <option value="accommodation">住宿</option>
            <option value="car">租车</option>
            <option value="other">其他</option>
          </select>
        </Field>
        <Field label="对应行程里的哪项安排（选填）">
          <select className={inputClass} value={itemId} onChange={(e) => setItemId(e.target.value)}>
            <option value="">不关联</option>
            {items.map((i) => (
              <option key={i.id} value={i.id}>
                {shortDate(i.date)} {i.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="日期（选填）" hint="关联了安排就不用填">
          <input type="date" className={inputClass} value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
      </div>
      <Field label="备注（选填）">
        <input className={inputClass} maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="例如 入口出示二维码；滑车第一圈 14:00 前扫码" />
      </Field>
      {error && <ErrorBox>{error}</ErrorBox>}
      <div className="flex gap-2">
        <Button variant="primary" busy={busy} disabled={!file || busy} onClick={submit}>
          存进凭证夹
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}

export function VoucherPanel({
  tripId,
  memberId,
  memberRole,
  vouchers,
  error,
  items,
  onRetry,
  onChanged,
  onRecognize,
}: {
  tripId: string;
  memberId: string;
  memberRole: string;
  vouchers: Voucher[] | null;
  error: string | null;
  items: ItemOption[];
  onRetry: () => void;
  onChanged: () => void;
  onRecognize: () => void;
}) {
  const [storing, setStoring] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  async function remove(v: Voucher) {
    setConfirming(null);
    setDeleting(v.id);
    setActionError(null);
    try {
      await api.deleteVoucher(tripId, v.id, memberId);
      onChanged();
    } catch (e) {
      setActionError(errorText(e));
    } finally {
      setDeleting(null);
    }
  }

  // 按日期分组，没日期的放最后
  const groups = new Map<string, Voucher[]>();
  for (const v of vouchers ?? []) {
    const key = v.date ?? "";
    groups.set(key, [...(groups.get(key) ?? []), v]);
  }

  return (
    <div className="space-y-5">
      <Section
        title="凭证夹"
        hint="大家订好的机票、住宿、租车、门票都放在这里，所有人都能查看和下载。下载行程包时会和 Word、日历一起打包。"
        action={
          <a className="inline-flex min-h-9 items-center rounded-lg px-3 text-sm font-medium text-accent hover:bg-accent-soft" href={api.bundleUrl(tripId)} download>
            下载行程包
          </a>
        }
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <Button variant="primary" onClick={onRecognize}>
            上传并识别（猪猪对照行程更新）
          </Button>
          {!storing && <Button onClick={() => setStoring(true)}>只存档</Button>}
        </div>
        <p className="text-xs text-ink-faint">
          「上传并识别」会读出时间、确认号，交给猪猪和行程比对、锁定对应安排；「只存档」不花时间，适合门票二维码、入场票这类只需要出示的文件。
        </p>
        {storing && (
          <StoreForm
            tripId={tripId}
            memberId={memberId}
            items={items}
            onCancel={() => setStoring(false)}
            onDone={() => {
              setStoring(false);
              onChanged();
            }}
          />
        )}
        {actionError && <ErrorBox>{actionError}</ErrorBox>}
      </Section>

      {!vouchers ? (
        error ? (
          <ErrorBox onRetry={onRetry}>{error}</ErrorBox>
        ) : (
          <div className="flex items-center gap-2 py-6 text-ink-soft">
            <Spinner /> 正在读取凭证…
          </div>
        )
      ) : vouchers.length === 0 ? (
        <p className="rounded-xl border border-dashed border-line bg-white px-4 py-6 text-center text-ink-soft">
          还没有凭证。订好一项就传上来，大家都能看到。
        </p>
      ) : (
        [...groups.entries()].map(([day, rows]) => (
          <section key={day || "none"} className="space-y-2">
            <h3 className="px-1 text-sm font-semibold text-ink-soft">{day ? shortDate(day, true) : "没有日期"}</h3>
            <ul className="space-y-2">
              {rows.map((v) => {
                const canDelete = v.uploaded_by === memberId || CAN_DELETE_ANY.includes(memberRole);
                return (
                  <li key={v.id} className="rounded-xl border border-line bg-white p-3 sm:p-4">
                    <div className="flex items-start gap-3">
                      <span className="mt-0.5 text-xl" aria-hidden>
                        {KIND_ICON[v.kind] ?? "📄"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium">{v.title ?? v.filename ?? v.id}</span>
                          <Badge>{v.kind_label}</Badge>
                        </div>
                        {v.items.length > 0 && <div className="text-sm text-ink-soft">对应：{v.items.join("、")}</div>}
                        {v.confirmation_nos.length > 0 && <div className="text-sm text-ink-soft">确认号：{v.confirmation_nos.join("、")}</div>}
                        {v.notes && <div className="text-sm text-ink-soft">备注：{v.notes}</div>}
                        <div className="mt-0.5 text-xs text-ink-faint">
                          {v.uploaded_by_name ? `${v.uploaded_by_name} 上传` : "对话里记录"}
                          {v.filename && ` · ${v.filename}`}
                          {v.size ? ` · ${size(v.size)}` : ""}
                        </div>
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2 sm:pl-9">
                      {v.has_file ? (
                        <>
                          <a
                            className="inline-flex min-h-9 items-center rounded-lg border border-line px-3 text-sm hover:bg-canvas"
                            href={api.voucherFileUrl(tripId, v.id)}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            查看
                          </a>
                          <a
                            className="inline-flex min-h-9 items-center rounded-lg border border-line px-3 text-sm hover:bg-canvas"
                            href={api.voucherFileUrl(tripId, v.id, true)}
                            download
                          >
                            下载
                          </a>
                        </>
                      ) : (
                        <span className="text-sm text-ink-faint">没有原文件</span>
                      )}
                      {canDelete &&
                        (confirming === v.id ? (
                          <>
                            <Button variant="danger" className="min-h-9 px-3 text-sm" onClick={() => remove(v)}>
                              确认删除（找不回来）
                            </Button>
                            <Button variant="ghost" className="min-h-9 px-3 text-sm" onClick={() => setConfirming(null)}>
                              取消
                            </Button>
                          </>
                        ) : (
                          <Button variant="ghost" className="min-h-9 px-3 text-sm text-red-700" busy={deleting === v.id} onClick={() => setConfirming(v.id)}>
                            删除
                          </Button>
                        ))}
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
