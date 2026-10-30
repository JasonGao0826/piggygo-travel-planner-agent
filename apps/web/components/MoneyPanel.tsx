"use client";

import { useState } from "react";
import {
  api,
  errorText,
  type ExpenseCategory,
  type ExpenseIn,
  type ExpenseView,
  type MemberBrief,
  type MoneySummary,
} from "@/lib/api";
import { shortDate } from "@/lib/format";
import { Section } from "./forms";
import { Avatar, Badge, Button, ErrorBox, Field, Spinner, cx, fieldClass, inputClass } from "./ui";
import type { ItemOption } from "./VoucherPanel";

const CATEGORIES: { value: ExpenseCategory; label: string }[] = [
  { value: "food", label: "餐饮" },
  { value: "grocery", label: "超市" },
  { value: "accommodation", label: "住宿" },
  { value: "activity", label: "活动" },
  { value: "car", label: "租车" },
  { value: "fuel", label: "油费" },
  { value: "parking", label: "停车" },
  { value: "flight", label: "机票" },
  { value: "visa", label: "签证与入境" },
  { value: "other", label: "其他" },
];
const CURRENCIES = ["NZD", "AUD", "CNY", "USD"];
const FX_LABEL = { same: "", charged: "实际扣款", cash: "现金按汇率", estimate: "暂估" } as const;

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// ---------- 记一笔 / 改一笔 ----------

interface FormState {
  title: string;
  category: ExpenseCategory;
  payer: string;
  amount: string;
  currency: string;
  date: string;
  fx: "charged" | "estimate" | "cash";
  charged_amount: string;
  charged_currency: string;
  method: "equal" | "shares" | "exact";
  members: Record<string, boolean>;
  values: Record<string, string>;
  item_id: string;
  note: string;
}

function initialForm(members: MemberBrief[], me: string, settle: string, e?: ExpenseView): FormState {
  const inSplit = new Set(e ? e.splits.map((s) => s.member_id) : members.map((m) => m.id));
  return {
    title: e?.title ?? "",
    category: e?.category ?? "food",
    payer: e?.payer ?? me,
    amount: e ? String(Number(e.amount)) : "",
    currency: e?.currency ?? "NZD",
    date: e?.date ?? today(),
    fx: e && e.fx.method !== "same" ? e.fx.method : "charged",
    charged_amount: e?.fx.charged_amount ? String(Number(e.fx.charged_amount)) : "",
    charged_currency: e?.fx.charged_currency ?? settle,
    method: e?.split_method ?? "equal",
    members: Object.fromEntries(members.map((m) => [m.id, inSplit.has(m.id)])),
    values: Object.fromEntries(
      members.map((m) => {
        const s = e?.splits.find((x) => x.member_id === m.id);
        return [m.id, s?.shares ? String(Number(s.shares)) : s?.amount ? String(Number(s.amount)) : ""];
      }),
    ),
    item_id: e?.item_id ?? "",
    note: e?.note ?? "",
  };
}

function ExpenseForm({
  members,
  me,
  settle,
  items,
  editing,
  busy,
  onSubmit,
  onCancel,
}: {
  members: MemberBrief[];
  me: string;
  settle: string;
  items: ItemOption[];
  editing?: ExpenseView;
  busy: boolean;
  onSubmit: (e: ExpenseIn) => void;
  onCancel: () => void;
}) {
  const [f, setF] = useState<FormState>(() => initialForm(members, me, settle, editing));
  const set = (patch: Partial<FormState>) => setF((s) => ({ ...s, ...patch }));
  const foreign = f.currency !== settle;
  const chosen = members.filter((m) => f.members[m.id]);
  const valid =
    f.title.trim() && Number(f.amount) !== 0 && chosen.length > 0 && (!foreign || f.fx !== "charged" || Number(f.charged_amount) > 0);

  function submit() {
    const splits =
      f.method === "equal"
        ? chosen.map((m) => ({ member_id: m.id }))
        : chosen.map((m) => ({
            member_id: m.id,
            shares: f.method === "shares" ? f.values[m.id] || "1" : null,
            amount: f.method === "exact" ? f.values[m.id] || "0" : null,
          }));
    onSubmit({
      title: f.title.trim(),
      category: f.category,
      payer: f.payer,
      amount: f.amount,
      currency: f.currency,
      date: f.date,
      split_method: f.method,
      splits,
      fx_method: foreign ? f.fx : "auto",
      charged_amount: foreign && f.fx === "charged" ? f.charged_amount : null,
      charged_currency: foreign && f.fx === "charged" ? f.charged_currency : null,
      item_id: f.item_id || null,
      note: f.note,
    });
  }

  return (
    <div className="space-y-3 rounded-lg border border-accent-line bg-accent-soft/40 p-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="花在哪里">
          <input className={inputClass} maxLength={100} value={f.title} onChange={(e) => set({ title: e.target.value })} placeholder="例如 皇后镇晚餐" />
        </Field>
        <Field label="类别">
          <select className={inputClass} value={f.category} onChange={(e) => set({ category: e.target.value as ExpenseCategory })}>
            {CATEGORIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="谁付的">
          <select className={inputClass} value={f.payer} onChange={(e) => set({ payer: e.target.value })}>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="付款日期">
          <input type="date" className={inputClass} value={f.date} onChange={(e) => set({ date: e.target.value })} />
        </Field>
        <Field label="金额（原币）" hint="退款填负数">
          <div className="flex gap-2">
            <input
              type="number"
              inputMode="decimal"
              step="0.01"
              className={cx(fieldClass, "min-w-0 flex-1")}
              value={f.amount}
              onChange={(e) => set({ amount: e.target.value })}
            />
            <select className={cx(fieldClass, "w-24 shrink-0")} value={f.currency} onChange={(e) => set({ currency: e.target.value })} aria-label="币种">
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </div>
        </Field>
        <Field label="对应行程里的安排（选填）" hint="关联后默认由参加的人分摊">
          <select className={inputClass} value={f.item_id} onChange={(e) => set({ item_id: e.target.value })}>
            <option value="">不关联</option>
            {items.map((i) => (
              <option key={i.id} value={i.id}>
                {shortDate(i.date)} {i.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {foreign && (
        <div className="space-y-2 rounded-lg bg-white p-3">
          <div className="text-sm font-medium">怎么折算成 {settle}</div>
          <div className="flex flex-wrap gap-2">
            {(
              [
                ["charged", "已出账：填实际扣款"],
                ["estimate", "刷卡还没出账：先暂估"],
                ["cash", "付现金：按当天汇率"],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                type="button"
                onClick={() => set({ fx: v })}
                className={cx(
                  "min-h-9 rounded-lg border px-3 text-sm",
                  f.fx === v ? "border-accent bg-accent-soft font-medium text-accent" : "border-line",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          {f.fx === "charged" ? (
            <div className="flex gap-2">
              <input
                type="number"
                inputMode="decimal"
                step="0.01"
                className={cx(fieldClass, "min-w-0 flex-1")}
                value={f.charged_amount}
                onChange={(e) => set({ charged_amount: e.target.value })}
                placeholder="银行账单上实际扣的金额"
              />
              <select
                className={cx(fieldClass, "w-24 shrink-0")}
                value={f.charged_currency}
                onChange={(e) => set({ charged_currency: e.target.value })}
                aria-label="扣款币种"
              >
                {[settle, ...CURRENCIES.filter((c) => c !== settle)].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </div>
          ) : (
            <p className="text-xs text-ink-faint">
              {f.fx === "estimate" ? "先按付款当天的央行参考汇率暂估，出账后记得回来补填实际扣款。" : "按付款当天的央行参考汇率折算，这就是最终金额。"}
            </p>
          )}
        </div>
      )}

      <div className="space-y-2 rounded-lg bg-white p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">谁来分摊</span>
          <select
            className={cx(fieldClass, "min-h-9 py-1 text-sm")}
            value={f.method}
            onChange={(e) => set({ method: e.target.value as FormState["method"] })}
            aria-label="分摊方式"
          >
            <option value="equal">均分</option>
            <option value="shares">按份数</option>
            <option value="exact">逐人填金额</option>
          </select>
        </div>
        <ul className="space-y-1.5">
          {members.map((m) => (
            <li key={m.id} className="flex items-center gap-2">
              <label className="flex min-h-9 flex-1 items-center gap-2">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={!!f.members[m.id]}
                  onChange={(e) => set({ members: { ...f.members, [m.id]: e.target.checked } })}
                />
                {m.name}
              </label>
              {f.method !== "equal" && f.members[m.id] && (
                <input
                  type="number"
                  inputMode="decimal"
                  step={f.method === "shares" ? "0.5" : "0.01"}
                  className={cx(fieldClass, "w-28")}
                  value={f.values[m.id]}
                  onChange={(e) => set({ values: { ...f.values, [m.id]: e.target.value } })}
                  placeholder={f.method === "shares" ? "份数" : `金额 ${f.currency}`}
                  aria-label={`${m.name} 的${f.method === "shares" ? "份数" : "金额"}`}
                />
              )}
            </li>
          ))}
        </ul>
      </div>

      <Field label="备注（选填）">
        <input className={inputClass} maxLength={500} value={f.note} onChange={(e) => set({ note: e.target.value })} />
      </Field>
      <div className="flex gap-2">
        <Button variant="primary" busy={busy} disabled={!valid || busy} onClick={submit}>
          {editing ? "保存修改" : "记下这笔"}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}

// ---------- 一笔账 ----------

function ExpenseRow({
  e,
  me,
  myRole,
  settle,
  busy,
  onConfirm,
  onDispute,
  onActual,
  onEdit,
  onDelete,
}: {
  e: ExpenseView;
  me: string;
  myRole: string;
  settle: string;
  busy: boolean;
  onConfirm: () => void;
  onDispute: (note: string) => void;
  onActual: (amount: string, currency: string) => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const [mode, setMode] = useState<null | "dispute" | "actual" | "delete">(null);
  const [text, setText] = useState("");
  const [cur, setCur] = useState(settle);
  const canEdit = me === e.created_by || me === e.payer || myRole === "owner";
  const canConfirm = me === e.payer || myRole === "owner"; // 替别人记的账，要付款人本人或发起人确认入账
  return (
    <li className={cx("rounded-xl border bg-white p-3", e.status === "draft" ? "border-dashed border-accent-line" : "border-line")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-medium">{e.title}</span>
            <Badge>{e.category_label}</Badge>
            {e.status === "draft" && <Badge tone="accent">草稿</Badge>}
            {e.fx.method === "estimate" && <Badge tone="amber">暂估</Badge>}
            {e.status === "disputed" && <Badge tone="red">有疑问</Badge>}
          </div>
          <div className="text-sm text-ink-soft">
            {e.payer_name} 付 · {e.split_text}
            {e.item_title && ` · ${e.item_title}`}
          </div>
          <div className="text-xs text-ink-faint">{e.shares.map((s) => `${s.name} ${s.text}`).join("，")}</div>
          {e.dispute_note && <div className="text-sm text-red-700">疑问：{e.dispute_note}</div>}
          {e.note && <div className="text-xs text-ink-faint">备注：{e.note}</div>}
        </div>
        <div className="shrink-0 text-right">
          <div className="font-semibold">{e.amount_text}</div>
          {e.currency !== settle && (
            <div className="text-xs text-ink-soft">
              ≈ {e.settle_text}
              {FX_LABEL[e.fx.method] && `（${FX_LABEL[e.fx.method]}）`}
            </div>
          )}
        </div>
      </div>

      {mode === "dispute" && (
        <div className="mt-2 flex gap-2">
          <input className={cx(fieldClass, "min-w-0 flex-1")} value={text} onChange={(x) => setText(x.target.value)} placeholder="哪里不对？" />
          <Button className="min-h-9 px-3 text-sm" onClick={() => (onDispute(text), setMode(null))}>
            提交
          </Button>
        </div>
      )}
      {mode === "actual" && (
        <div className="mt-2 flex gap-2">
          <input
            type="number"
            inputMode="decimal"
            step="0.01"
            className={cx(fieldClass, "min-w-0 flex-1")}
            value={text}
            onChange={(x) => setText(x.target.value)}
            placeholder="银行账单上实际扣的金额"
          />
          <select className={cx(fieldClass, "w-20")} value={cur} onChange={(x) => setCur(x.target.value)} aria-label="扣款币种">
            {[settle, ...CURRENCIES.filter((c) => c !== settle)].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
          <Button className="min-h-9 px-3 text-sm" disabled={!(Number(text) > 0)} onClick={() => (onActual(text, cur), setMode(null))}>
            保存
          </Button>
        </div>
      )}

      <div className="mt-2 flex flex-wrap gap-1.5">
        {e.status === "draft" && canConfirm && (
          <Button variant="primary" className="min-h-9 px-3 text-sm" busy={busy} onClick={onConfirm}>
            确认入账
          </Button>
        )}
        {e.status === "draft" && !canConfirm && (
          <span className="self-center text-sm text-ink-faint">等 {e.payer_name} 确认入账</span>
        )}
        {e.status === "disputed" && canEdit && (
          <Button className="min-h-9 px-3 text-sm" busy={busy} onClick={onConfirm}>
            疑问已解决
          </Button>
        )}
        {e.fx.method === "estimate" && (me === e.payer || canEdit) && (
          <Button className="min-h-9 px-3 text-sm" onClick={() => (setText(""), setMode(mode === "actual" ? null : "actual"))}>
            补填实际扣款
          </Button>
        )}
        {e.status === "confirmed" && (
          <Button variant="ghost" className="min-h-9 px-3 text-sm" onClick={() => (setText(""), setMode(mode === "dispute" ? null : "dispute"))}>
            有疑问
          </Button>
        )}
        {canEdit && (
          <Button variant="ghost" className="min-h-9 px-3 text-sm" onClick={onEdit}>
            修改
          </Button>
        )}
        {canEdit &&
          (mode === "delete" ? (
            <>
              <Button variant="danger" className="min-h-9 px-3 text-sm" onClick={onDelete}>
                确认删除
              </Button>
              <Button variant="ghost" className="min-h-9 px-3 text-sm" onClick={() => setMode(null)}>
                取消
              </Button>
            </>
          ) : (
            <Button variant="ghost" className="min-h-9 px-3 text-sm text-red-700" onClick={() => setMode("delete")}>
              删除
            </Button>
          ))}
      </div>
    </li>
  );
}

// ---------- 页签 ----------

export function MoneyPanel({
  tripId,
  memberId,
  members,
  items,
  money,
  error,
  onRetry,
  onUpdated,
}: {
  tripId: string;
  memberId: string;
  members: MemberBrief[];
  items: ItemOption[];
  money: MoneySummary | null;
  error: string | null;
  onRetry: () => void;
  onUpdated: (m: MoneySummary) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [form, setForm] = useState<null | { editing?: ExpenseView }>(null);
  const [copied, setCopied] = useState(false);
  const [pair, setPair] = useState<[string, string]>(["", ""]);

  if (!money) {
    return error ? (
      <ErrorBox onRetry={onRetry}>{error}</ErrorBox>
    ) : (
      <div className="flex items-center gap-2 py-10 text-ink-soft">
        <Spinner /> 正在读取账本…
      </div>
    );
  }

  const me = members.find((m) => m.id === memberId);
  const myRole = me?.role ?? "member";
  const settle = money.settle_currency;
  const names = Object.fromEntries(members.map((m) => [m.id, m.name]));

  async function run(key: string, fn: () => Promise<MoneySummary>) {
    setBusy(key);
    setActionError(null);
    try {
      onUpdated(await fn());
      return true;
    } catch (e) {
      setActionError(errorText(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function copyText() {
    try {
      await navigator.clipboard.writeText(money!.text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setActionError("复制没成功，可以长按下面的文字手动复制");
    }
  }

  // 按日期分组
  const groups = new Map<string, ExpenseView[]>();
  for (const e of [...money.expenses].reverse()) groups.set(e.date, [...(groups.get(e.date) ?? []), e]);
  const pending = money.transfers.filter((t) => t.status === "sent");
  const warnings = money.checks.filter((c) => c.severity !== "info");
  const drafts = money.checks.find((c) => c.code === "DRAFT");

  return (
    <div className="space-y-5">
      {actionError && <ErrorBox>{actionError}</ErrorBox>}

      <Section
        title="谁欠谁"
        hint={`统一折算成 ${settle} 结算，按转账次数最少来算。草稿确认入账后才算进来。`}
        action={
          <a className="inline-flex min-h-9 items-center rounded-lg px-3 text-sm font-medium text-accent hover:bg-accent-soft" href={api.moneyCsvUrl(tripId)} download>
            导出 Excel
          </a>
        }
      >
        <div className="scroll-thin overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[420px] border-collapse text-sm">
            <thead>
              <tr className="bg-gray-50 text-left text-ink-soft">
                <th className="px-3 py-2 font-semibold">成员</th>
                <th className="px-3 py-2 text-right font-semibold">付了</th>
                <th className="px-3 py-2 text-right font-semibold">应承担</th>
                <th className="px-3 py-2 text-right font-semibold">净额</th>
              </tr>
            </thead>
            <tbody>
              {money.balances.map((b, i) => (
                <tr key={b.member_id} className="border-t border-line/70">
                  <td className="px-3 py-2">
                    <span className="flex items-center gap-2">
                      <Avatar name={b.name} index={i} size={24} />
                      {b.name}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right">{b.paid_text}</td>
                  <td className="px-3 py-2 text-right">{b.owed_text}</td>
                  <td className={cx("px-3 py-2 text-right font-semibold", Number(b.net) > 0 ? "text-emerald-700" : Number(b.net) < 0 ? "text-red-700" : "")}>
                    {b.net_text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="text-xs text-ink-faint">净额是正数表示别人欠他，负数表示他欠别人。</p>

        <div className="space-y-2">
          <h3 className="text-sm font-semibold">结算方案</h3>
          {money.plan.length === 0 ? (
            <p className="text-sm text-ink-soft">现在大家已经两清了。</p>
          ) : (
            <ul className="space-y-1.5">
              {money.plan.map((p, i) => (
                <li key={i} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-canvas px-3 py-2">
                  <span className="font-medium">{p.text}</span>
                  {p.from.includes(memberId) && (
                    <Button
                      className="min-h-9 px-3 text-sm"
                      busy={busy === `pay${i}`}
                      onClick={() => run(`pay${i}`, () => api.addTransfer(tripId, memberId, memberId, p.receive_member, p.amount))}
                    >
                      我已转账
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <Button className="min-h-9 px-3 text-sm" onClick={copyText}>
            {copied ? "已复制" : "复制结算文字（发到微信群）"}
          </Button>
        </div>

        {pending.length > 0 && (
          <div className="space-y-1.5">
            <h3 className="text-sm font-semibold">等待确认的还款</h3>
            {pending.map((t) => (
              <div key={t.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm">
                <span>
                  {t.from_name} → {t.to_name} {t.text}，等 {t.to_name} 确认收到
                </span>
                <span className="flex gap-1.5">
                  {t.to_member === memberId && (
                    <Button className="min-h-9 px-3 text-sm" onClick={() => run(t.id, () => api.confirmTransfer(tripId, t.id, memberId))}>
                      确认收到
                    </Button>
                  )}
                  {(t.from_member === memberId || t.to_member === memberId) && (
                    <Button variant="ghost" className="min-h-9 px-3 text-sm" onClick={() => run(t.id, () => api.deleteTransfer(tripId, t.id, memberId))}>
                      撤销
                    </Button>
                  )}
                </span>
              </div>
            ))}
          </div>
        )}

        {(warnings.length > 0 || drafts) && (
          <ul className="space-y-1 text-sm">
            {[...warnings, ...(drafts ? [drafts] : [])].map((c, i) => (
              <li key={i} className={cx("flex gap-2", c.severity === "info" ? "text-ink-soft" : "text-amber-900")}>
                <span className={cx("mt-[7px] h-2 w-2 shrink-0 rounded-full", c.severity === "info" ? "bg-accent" : "bg-amber-500")} aria-hidden />
                {c.message}
              </li>
            ))}
          </ul>
        )}

        {money.by_category.length > 0 && (
          <p className="text-sm text-ink-soft">
            一共 {money.total_text}：{money.by_category.map((c) => `${c.label} ${c.text}`).join("，")}
          </p>
        )}
      </Section>

      <Section
        title="账目"
        hint="谁付的、多少钱、谁分摊。也可以直接跟猪猪说「晚饭我付了 NZ$128，天哥没吃」。"
        action={
          !form && (
            <Button variant="primary" className="min-h-9 px-3 text-sm" onClick={() => setForm({})}>
              ＋ 记一笔
            </Button>
          )
        }
      >
        {form && (
          <ExpenseForm
            key={form.editing?.id ?? "new"}
            members={members}
            me={memberId}
            settle={settle}
            items={items}
            editing={form.editing}
            busy={busy === "form"}
            onCancel={() => setForm(null)}
            onSubmit={async (data) => {
              const ok = await run("form", () =>
                form.editing ? api.editExpense(tripId, form.editing.id, memberId, data) : api.addExpense(tripId, memberId, data),
              );
              if (ok) setForm(null);
            }}
          />
        )}
        {money.expenses.length === 0 && !form && <p className="text-sm text-ink-faint">还没有账目。</p>}
        {[...groups.entries()].map(([day, rows]) => (
          <div key={day} className="space-y-2">
            <h3 className="px-1 text-sm font-semibold text-ink-soft">{shortDate(day, true)}</h3>
            <ul className="space-y-2">
              {rows.map((e) => (
                <ExpenseRow
                  key={e.id}
                  e={e}
                  me={memberId}
                  myRole={myRole}
                  settle={settle}
                  busy={busy === e.id}
                  onConfirm={() => run(e.id, () => api.confirmExpense(tripId, e.id, memberId))}
                  onDispute={(note) => run(e.id, () => api.disputeExpense(tripId, e.id, memberId, note))}
                  onActual={(amount, currency) => run(e.id, () => api.setActual(tripId, e.id, memberId, amount, currency))}
                  onEdit={() => setForm({ editing: e })}
                  onDelete={() => run(e.id, () => api.deleteExpense(tripId, e.id, memberId))}
                />
              ))}
            </ul>
          </div>
        ))}
      </Section>

      <Section title="合并结算" hint="情侣或一家人可以合成一方结算：分摊时仍按人算，转账时合在一起，次数更少。">
        {money.units.length > 0 ? (
          <ul className="flex flex-wrap gap-2">
            {money.units.map((u, i) => (
              <li key={i} className="inline-flex items-center gap-1.5 rounded-full bg-piggy-soft px-3 py-1 text-sm">
                {u.map((m) => names[m] ?? m).join(" + ")}
                {["owner", "editor"].includes(myRole) && (
                  <button
                    type="button"
                    className="text-ink-faint hover:text-red-700"
                    aria-label="取消合并"
                    onClick={() => run("units", () => api.setUnits(tripId, memberId, money.units.filter((_, k) => k !== i)))}
                  >
                    ×
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-faint">现在每个人各自结算。</p>
        )}
        {["owner", "editor"].includes(myRole) && (
          <div className="flex flex-wrap items-center gap-2">
            {[0, 1].map((k) => (
              <select
                key={k}
                className={cx(fieldClass, "w-32")}
                value={pair[k]}
                onChange={(e) => setPair(k === 0 ? [e.target.value, pair[1]] : [pair[0], e.target.value])}
                aria-label={`第 ${k + 1} 个人`}
              >
                <option value="">选成员</option>
                {members
                  .filter((m) => !money.units.flat().includes(m.id))
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.name}
                    </option>
                  ))}
              </select>
            ))}
            <Button
              className="min-h-10 px-3 text-sm"
              disabled={!pair[0] || !pair[1] || pair[0] === pair[1]}
              busy={busy === "units"}
              onClick={async () => {
                if (await run("units", () => api.setUnits(tripId, memberId, [...money.units, [pair[0], pair[1]]]))) setPair(["", ""]);
              }}
            >
              合并
            </Button>
          </div>
        )}
      </Section>
    </div>
  );
}
