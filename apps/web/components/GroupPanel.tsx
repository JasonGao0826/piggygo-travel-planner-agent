"use client";

import { useEffect, useState } from "react";
import {
  api,
  errorText,
  type Divergence,
  type GroupView,
  type Guide,
  type MemberPreferences,
  type Pace,
  type Poll,
  type PollOptionDraft,
  type PreferenceForm,
  type ThrillLevel,
} from "@/lib/api";
import { ROLE_LABEL } from "@/lib/format";
import { Choice, Collapsible, Section, TagInput } from "./forms";
import { GuideCard, GuideReader } from "./GuideReader";
import { MustDoEditor, emptyMustDo, type MustDoForm } from "./IntakeWizard";
import { Avatar, Badge, Button, ErrorBox, Field, Spinner, cx, fieldClass, inputClass } from "./ui";

const PACE_LABEL: Record<Pace, string> = { relaxed: "悠闲", balanced: "适中", packed: "排满" };
const THRILL_LABEL: Record<ThrillLevel, string> = { low: "低", mid: "中", high: "高" };
const CAN_MANAGE = ["owner", "editor"];

// ---------- 我的偏好：表单状态（全部用字符串，保存时再转换） ----------

interface PrefState {
  must_do: MustDoForm[];
  wishlist: string[];
  avoid: string[];
  thrill_level: "" | ThrillLevel;
  pace: "" | Pace;
  max_daily_drive_hours: string;
  earliest_start: string;
  budget_amount: string;
  budget_currency: string;
  diet: string[];
  notes: string;
}

function toState(p: MemberPreferences | undefined): PrefState {
  return {
    must_do: (p?.must_do ?? []).map((d) => ({
      ...emptyMustDo(),
      what: d.what,
      place_hint: d.place_hint ?? "",
      date: d.date ?? "",
      fixed_activity: d.fixed_activity,
      fixed_place: d.fixed_place,
      fixed_date: d.fixed_date,
    })),
    wishlist: p?.wishlist ?? [],
    avoid: p?.avoid ?? [],
    thrill_level: p?.thrill_level ?? "",
    pace: p?.pace ?? "",
    max_daily_drive_hours: p?.max_daily_drive_hours != null ? String(p.max_daily_drive_hours) : "",
    earliest_start: p?.earliest_start ? p.earliest_start.slice(0, 5) : "",
    budget_amount: p?.budget_per_person ? String(Number(p.budget_per_person.amount)) : "",
    budget_currency: p?.budget_per_person?.currency ?? "AUD",
    diet: p?.diet ?? [],
    notes: p?.notes ?? "",
  };
}

function toForm(s: PrefState): PreferenceForm {
  const hours = Number(s.max_daily_drive_hours);
  const budget = Number(s.budget_amount);
  return {
    must_do: s.must_do
      .filter((d) => d.what.trim())
      .map((d) => ({
        what: d.what.trim(),
        place_hint: d.place_hint.trim() || null,
        date: d.date || null,
        fixed_activity: d.fixed_activity,
        fixed_place: d.fixed_place,
        fixed_date: d.fixed_date,
      })),
    wishlist: s.wishlist,
    avoid: s.avoid,
    thrill_level: s.thrill_level || null,
    pace: s.pace || null,
    max_daily_drive_hours: s.max_daily_drive_hours.trim() && hours > 0 ? hours : null,
    earliest_start: s.earliest_start || null,
    budget_per_person: s.budget_amount.trim() && budget > 0 ? { amount: budget, currency: s.budget_currency } : null,
    diet: s.diet,
    notes: s.notes.trim(),
  };
}

function MyPreferences({
  tripId,
  memberId,
  memberName,
  prefs,
  saving,
  onSave,
  onGuideRead,
}: {
  tripId: string;
  memberId: string;
  memberName: string;
  prefs: MemberPreferences | undefined;
  saving: boolean;
  onSave: (form: PreferenceForm) => void;
  onGuideRead: (g: Guide) => void;
}) {
  const [state, setState] = useState<PrefState>(() => toState(prefs));
  const [dirty, setDirty] = useState(false);
  // 切换成员或别处保存后，用最新的数据重置表单
  useEffect(() => {
    setState(toState(prefs));
    setDirty(false);
  }, [prefs]);
  const set = (patch: Partial<PrefState>) => {
    setState((s) => ({ ...s, ...patch }));
    setDirty(true);
  };

  return (
    <Section
      title={`我的偏好（${memberName}）`}
      hint="只填你自己的想法，都可以不填。猪猪每次对话都会看到大家的偏好，改行程时会照顾到每个人。"
    >
      <div className="space-y-2">
        <div>
          <div className="text-sm font-medium">必玩项目</div>
          <p className="text-xs text-ink-faint">汇总时会记进决策账本，猪猪改行程时会照着来。</p>
        </div>
        {state.must_do.map((d, j) => (
          <MustDoEditor
            key={d.key}
            item={d}
            onChange={(v) => set({ must_do: state.must_do.map((x, k) => (k === j ? v : x)) })}
            onRemove={() => set({ must_do: state.must_do.filter((_, k) => k !== j) })}
          />
        ))}
        <Button onClick={() => set({ must_do: [...state.must_do, emptyMustDo()] })} className="w-full sm:w-auto">
          ＋ 添加必玩项目
        </Button>
      </div>

      <GuideReader
        tripId={tripId}
        memberId={memberId}
        onRead={onGuideRead}
        onWish={(name) => set({ wishlist: state.wishlist.includes(name) ? state.wishlist : [...state.wishlist, name] })}
        onMust={(name, hint) =>
          set({ must_do: [...state.must_do, { ...emptyMustDo(), what: name, place_hint: hint ?? "", fixed_place: !!hint }] })
        }
      />

      <Field group label="想去的地方、想玩的项目" hint="也可以直接写，或者贴链接">
        <TagInput value={state.wishlist} onChange={(v) => set({ wishlist: v })} placeholder="输入后回车" />
      </Field>
      <Field group label="不想玩或不敢玩的">
        <TagInput
          value={state.avoid}
          onChange={(v) => set({ avoid: v })}
          placeholder="输入后回车"
          suggestions={["怕高", "怕水", "晕车", "晕船", "不想太早起"]}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field group label="节奏">
          <Choice
            value={state.pace}
            onChange={(v) => set({ pace: v })}
            columns={3}
            options={[
              { value: "relaxed", label: "悠闲" },
              { value: "balanced", label: "适中" },
              { value: "packed", label: "排满" },
            ]}
          />
        </Field>
        <Field group label="刺激项目接受度">
          <Choice
            value={state.thrill_level}
            onChange={(v) => set({ thrill_level: v })}
            columns={3}
            options={[
              { value: "low", label: "低" },
              { value: "mid", label: "中" },
              { value: "high", label: "高" },
            ]}
          />
        </Field>
      </div>

      <Collapsible title="开车、出发时间、预算、饮食（选填）">
        <div className="grid grid-cols-2 gap-3">
          <Field label="每天最多开车（小时）">
            <input
              type="number"
              inputMode="decimal"
              min={1}
              max={14}
              className={inputClass}
              value={state.max_daily_drive_hours}
              onChange={(e) => set({ max_daily_drive_hours: e.target.value })}
            />
          </Field>
          <Field label="早上最早几点出发">
            <input type="time" className={inputClass} value={state.earliest_start} onChange={(e) => set({ earliest_start: e.target.value })} />
          </Field>
        </div>
        <Field label="每人预算（不含机票也行，写在备注里）">
          <div className="flex gap-2">
            <input
              type="number"
              inputMode="decimal"
              className={cx(fieldClass, "min-w-0 flex-1")}
              value={state.budget_amount}
              onChange={(e) => set({ budget_amount: e.target.value })}
            />
            <select
              className={cx(fieldClass, "w-24 shrink-0")}
              value={state.budget_currency}
              onChange={(e) => set({ budget_currency: e.target.value })}
              aria-label="币种"
            >
              <option value="AUD">AUD</option>
              <option value="NZD">NZD</option>
              <option value="CNY">CNY</option>
            </select>
          </div>
        </Field>
        <Field group label="饮食">
          <TagInput maxItems={20} value={state.diet} onChange={(v) => set({ diet: v })} placeholder="输入后回车" suggestions={["素食", "不吃辣", "海鲜过敏"]} />
        </Field>
        <Field label="其他想说的">
          <textarea className={cx(inputClass, "min-h-20")} maxLength={1000} value={state.notes} onChange={(e) => set({ notes: e.target.value })} />
        </Field>
      </Collapsible>

      <div className="flex items-center gap-3">
        <Button variant="primary" busy={saving} disabled={saving || !dirty} onClick={() => onSave(toForm(state))}>
          {saving ? "正在保存…" : "保存我的偏好"}
        </Button>
        {prefs && !dirty && <span className="text-sm text-ink-faint">已保存</span>}
        {dirty && <span className="text-sm text-amber-700">还没保存</span>}
      </div>
    </Section>
  );
}

// ---------- 别人的偏好（只读） ----------

function prefLines(p: MemberPreferences): string[] {
  const lines: string[] = [];
  if (p.must_do.length) lines.push(`必玩：${p.must_do.map((d) => d.what + (d.place_hint ? `（${d.place_hint}）` : "")).join("、")}`);
  if (p.wishlist.length) lines.push(`想去：${p.wishlist.join("、")}`);
  if (p.avoid.length) lines.push(`不想玩：${p.avoid.join("、")}`);
  const misc = [
    p.pace && `节奏${PACE_LABEL[p.pace]}`,
    p.thrill_level && `刺激接受度${THRILL_LABEL[p.thrill_level]}`,
    p.max_daily_drive_hours && `每天开车 ≤ ${p.max_daily_drive_hours} 小时`,
    p.earliest_start && `${p.earliest_start.slice(0, 5)} 以后出发`,
    p.budget_per_person && `预算 ${Number(p.budget_per_person.amount).toLocaleString()} ${p.budget_per_person.currency}`,
  ].filter(Boolean);
  if (misc.length) lines.push(misc.join("；"));
  if (p.diet.length) lines.push(`饮食：${p.diet.join("、")}`);
  if (p.notes) lines.push(`备注：${p.notes}`);
  return lines;
}

// ---------- 投票 ----------

function PollCard({
  poll,
  names,
  memberId,
  canClose,
  canDecide,
  busy,
  onVote,
  onClose,
  onAsk,
}: {
  poll: Poll;
  names: Record<string, string>;
  memberId: string;
  canClose: boolean;
  canDecide: boolean; // 发起人、编辑者可以拍板；普通成员只能按票数结束自己发起的投票
  busy: boolean;
  onVote: (optionId: string) => void;
  onClose: (optionId?: string) => void;
  onAsk: (text: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const mine = poll.votes[memberId];
  const total = Object.keys(poll.votes).length;
  const open = poll.status === "open";
  const waiting = Object.keys(names).filter((m) => !(m in poll.votes));
  const result = poll.options.find((o) => o.id === poll.result);
  return (
    <div className={cx("space-y-3 rounded-lg border p-3", open ? "border-accent-line bg-white" : "border-line bg-canvas/60")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-medium">{poll.question}</div>
          <div className="text-xs text-ink-faint">
            {names[poll.created_by ?? ""] ?? "猪猪"} 发起
            {poll.deadline && open && ` · ${new Date(poll.deadline).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} 截止`}
          </div>
        </div>
        <Badge tone={open ? "accent" : "green"}>{open ? "进行中" : "已结束"}</Badge>
      </div>
      <ul className="space-y-2">
        {poll.options.map((o) => {
          const voters = Object.entries(poll.votes)
            .filter(([, v]) => v === o.id)
            .map(([m]) => names[m] ?? m);
          const share = total ? Math.round((voters.length / total) * 100) : 0;
          const chosen = mine === o.id;
          const won = poll.result === o.id;
          return (
            <li key={o.id}>
              <button
                type="button"
                disabled={!open || busy || (picking ? false : chosen)}
                onClick={() => (picking ? (setPicking(false), onClose(o.id)) : onVote(o.id))}
                className={cx(
                  "relative w-full overflow-hidden rounded-lg border px-3 py-2 text-left transition-colors",
                  won ? "border-emerald-300" : chosen ? "border-accent" : "border-line",
                  open && !chosen && "hover:border-accent-line",
                  picking && "ring-2 ring-amber-300",
                )}
              >
                <span
                  className={cx("absolute inset-y-0 left-0", won ? "bg-emerald-50" : "bg-accent-soft")}
                  style={{ width: `${share}%` }}
                  aria-hidden
                />
                <span className="relative flex items-start justify-between gap-2">
                  <span className="min-w-0">
                    <span className={cx("text-[15px]", (chosen || won) && "font-semibold")}>
                      {o.label}
                      {chosen && "（我投的）"}
                    </span>
                    {o.impact && <span className="block text-xs text-ink-soft">{o.impact}</span>}
                    {voters.length > 0 && <span className="block text-xs text-ink-faint">{voters.join("、")}</span>}
                  </span>
                  <span className="shrink-0 text-sm text-ink-soft">{voters.length} 票</span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {open ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {waiting.length > 0 && <span className="text-ink-faint">还没投：{waiting.map((m) => names[m]).join("、")}</span>}
          {canClose && (
            <span className="ml-auto flex gap-2">
              {picking ? (
                <>
                  <span className="text-amber-800">点一个选项作为最终结果</span>
                  <Button variant="ghost" className="min-h-9 px-2 text-sm" onClick={() => setPicking(false)}>
                    取消
                  </Button>
                </>
              ) : (
                <>
                  <Button variant="ghost" className="min-h-9 px-2 text-sm" disabled={busy || total === 0} onClick={() => onClose()}>
                    按票数结束
                  </Button>
                  {canDecide && (
                    <Button variant="ghost" className="min-h-9 px-2 text-sm" disabled={busy} onClick={() => setPicking(true)}>
                      我来拍板
                    </Button>
                  )}
                </>
              )}
            </span>
          )}
        </div>
      ) : (
        result && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <span className="text-emerald-800">
              结果：{result.label}
              {poll.note && `（${poll.note}）`}，已记入决策账本
            </span>
            <Button
              className="ml-auto min-h-9 px-3 text-sm"
              onClick={() => onAsk(`【投票结果】${poll.question} → ${result.label}。\n请按这个结果看看行程要不要调整，需要的话直接提出改动。`)}
            >
              让猪猪按结果调整
            </Button>
          </div>
        )
      )}
    </div>
  );
}

function NewPoll({
  initial,
  busy,
  onSubmit,
  onCancel,
}: {
  initial?: { question: string; options: PollOptionDraft[]; divergenceId?: string };
  busy: boolean;
  onSubmit: (question: string, options: PollOptionDraft[], deadlineHours: number | null) => void;
  onCancel: () => void;
}) {
  const [question, setQuestion] = useState(initial?.question ?? "");
  const [options, setOptions] = useState<PollOptionDraft[]>(
    initial?.options.length ? initial.options : [{ label: "", impact: "" }, { label: "", impact: "" }],
  );
  const [deadline, setDeadline] = useState("");
  const valid = question.trim() && options.filter((o) => o.label.trim()).length >= 2;
  return (
    <div className="space-y-3 rounded-lg border border-accent-line bg-accent-soft/40 p-3">
      <Field label="要大家决定什么">
        <input className={inputClass} value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="例如 第 5 天去瓦纳卡还是格林诺奇？" />
      </Field>
      {options.map((o, i) => (
        <div key={i} className="grid gap-2 sm:grid-cols-[1fr_1.4fr_auto]">
          <input
            className={inputClass}
            value={o.label}
            onChange={(e) => setOptions(options.map((x, k) => (k === i ? { ...x, label: e.target.value } : x)))}
            placeholder={`选项 ${i + 1}`}
            aria-label={`选项 ${i + 1}`}
          />
          <input
            className={inputClass}
            value={o.impact}
            onChange={(e) => setOptions(options.map((x, k) => (k === i ? { ...x, impact: e.target.value } : x)))}
            placeholder="选它会怎样（选填）"
            aria-label={`选项 ${i + 1} 的影响`}
          />
          {options.length > 2 && (
            <Button variant="ghost" onClick={() => setOptions(options.filter((_, k) => k !== i))}>
              删除
            </Button>
          )}
        </div>
      ))}
      <div className="flex flex-wrap items-end gap-2">
        {options.length < 4 && (
          <Button variant="ghost" onClick={() => setOptions([...options, { label: "", impact: "" }])}>
            ＋ 加一个选项
          </Button>
        )}
        <Field label="截止" className="ml-auto">
          <select className={cx(fieldClass, "w-40")} value={deadline} onChange={(e) => setDeadline(e.target.value)}>
            <option value="">等大家都投完</option>
            <option value="12">12 小时后</option>
            <option value="24">1 天后</option>
            <option value="72">3 天后</option>
          </select>
        </Field>
      </div>
      <div className="flex gap-2">
        <Button variant="primary" busy={busy} disabled={!valid || busy} onClick={() => onSubmit(question, options, deadline ? Number(deadline) : null)}>
          发起投票
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          取消
        </Button>
      </div>
    </div>
  );
}

// ---------- 汇总 ----------

function DivergenceCard({
  d,
  names,
  onStartPoll,
}: {
  d: Divergence;
  names: Record<string, string>;
  onStartPoll: () => void;
}) {
  return (
    <li className="space-y-1.5 rounded-lg border border-amber-200 bg-amber-50/60 px-3 py-2.5">
      <div className="font-medium text-amber-950">{d.topic}</div>
      <div className="text-sm text-ink">{d.detail}</div>
      {d.positions.length > 0 && (
        <ul className="space-y-0.5 text-sm">
          {d.positions.map((p, i) => (
            <li key={i} className="text-ink-soft">
              <span className="text-ink">{p.member_ids.map((m) => names[m] ?? m).join("、")}</span>：{p.stance}
            </li>
          ))}
        </ul>
      )}
      {d.suggestion && <div className="text-xs text-ink-soft">建议：{d.suggestion}</div>}
      <div className="pt-1">
        {d.poll_id ? (
          <span className="text-sm text-accent">已发起投票，在下面</span>
        ) : (
          <Button className="min-h-9 px-3 text-sm" onClick={onStartPoll}>
            发起投票
          </Button>
        )}
      </div>
    </li>
  );
}

// ---------- 页签 ----------

export function GroupPanel({
  tripId,
  memberId,
  group,
  error,
  onRetry,
  onUpdated,
  onAsk,
}: {
  tripId: string;
  memberId: string;
  group: GroupView | null;
  error: string | null;
  onRetry: () => void;
  onUpdated: (g: GroupView, decisionsChanged?: boolean) => void;
  onAsk: (text: string) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ question: string; options: PollOptionDraft[]; divergenceId?: string } | null>(null);
  const [guides, setGuides] = useState<Guide[] | null>(null);

  useEffect(() => {
    api.listGuides(tripId).then(setGuides, () => setGuides([]));
  }, [tripId]);

  if (!group) {
    return error ? (
      <ErrorBox onRetry={onRetry}>{error}</ErrorBox>
    ) : (
      <div className="flex items-center gap-2 py-10 text-ink-soft">
        <Spinner /> 正在读取大家的想法…
      </div>
    );
  }

  const names = Object.fromEntries(group.members.map((m) => [m.id, m.name]));
  const me = group.members.find((m) => m.id === memberId);
  const canManage = !!me && CAN_MANAGE.includes(me.role);
  const filledCount = group.members.filter((m) => m.filled).length;
  const summary = group.summary;
  const openPolls = group.polls.filter((p) => p.status === "open");
  const closedPolls = group.polls.filter((p) => p.status === "closed");

  async function run(key: string, fn: () => Promise<GroupView>, decisionsChanged = false) {
    setBusy(key);
    setActionError(null);
    try {
      onUpdated(await fn(), decisionsChanged);
      return true;
    } catch (e) {
      setActionError(errorText(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  const myPrefs = (
    <MyPreferences
      tripId={tripId}
      memberId={memberId}
      onGuideRead={(g) => setGuides((prev) => [g, ...(prev ?? []).filter((x) => x.id !== g.id)])}
      memberName={me?.name ?? "我"}
      prefs={group.preferences[memberId]}
      saving={busy === "prefs"}
      onSave={(form) => run("prefs", () => api.savePreferences(tripId, memberId, form))}
    />
  );
  const filledByMe = !!me && group.members.find((m) => m.id === memberId)?.filled;

  return (
    <div className="space-y-5">
      {actionError && <ErrorBox>{actionError}</ErrorBox>}

      <Section
        title="大家的想法"
        hint={`${filledCount}/${group.members.length} 人填了偏好${group.all_filled ? "，可以汇总了" : ""}`}
        action={
          canManage && (
            <Button
              variant={(group.all_filled && !summary) || group.summary_stale ? "primary" : "secondary"}
              busy={busy === "summary"}
              disabled={!!busy || filledCount === 0}
              onClick={() => run("summary", () => api.summarize(tripId, memberId), true)}
            >
              {busy === "summary" ? "正在汇总…" : summary ? "重新汇总" : "现在汇总"}
            </Button>
          )
        }
      >
        <ul className="grid gap-2 sm:grid-cols-2">
          {group.members.map((m, i) => {
            const p = group.preferences[m.id];
            const lines = p ? prefLines(p) : [];
            return (
              <li key={m.id} className="flex gap-3 rounded-lg border border-line px-3 py-2.5">
                <Avatar name={m.name} index={i} />
                <div className="min-w-0 text-sm">
                  <div className="flex items-center gap-1.5">
                    <span className="font-medium">{m.name}</span>
                    <span className="text-xs text-ink-faint">{ROLE_LABEL[m.role] ?? m.role}</span>
                    {m.filled ? <Badge tone="green">已填</Badge> : <Badge>还没填</Badge>}
                  </div>
                  {lines.length > 0 ? (
                    <ul className="mt-0.5 space-y-0.5 text-ink-soft">
                      {lines.map((l, k) => (
                        <li key={k}>{l}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-0.5 text-ink-faint">{m.id === memberId ? "在下面填你的偏好" : "等 TA 打开行程自己填"}</p>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
        {busy === "summary" && <p className="text-sm text-ink-soft">猪猪正在比较大家的想法，通常要 30–60 秒…</p>}
        {!canManage && !summary && <p className="text-sm text-ink-faint">大家填好后，由发起人点「现在汇总」。</p>}
      </Section>

      {/* 自己还没填时，先填偏好；填过了就放到最后 */}
      {!filledByMe && myPrefs}

      {summary && (
        <Section
          title="汇总结果"
          hint={
            <>
              {new Date(summary.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} 汇总
              {summary.missing.length > 0 && `，当时 ${summary.missing.map((m) => names[m]).join("、")} 还没填`}
              {group.summary_stale && <span className="text-amber-700">。之后有人改了偏好，可以重新汇总</span>}
            </>
          }
        >
          {summary.divergences.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-amber-800">分歧（{summary.divergences.length}）</h3>
              <ul className="space-y-2">
                {summary.divergences.map((d) => (
                  <DivergenceCard
                    key={d.id}
                    d={d}
                    names={names}
                    onStartPoll={() =>
                      setDraft({
                        question: d.poll?.question ?? d.topic,
                        options: d.poll?.options ?? [],
                        divergenceId: d.id,
                      })
                    }
                  />
                ))}
              </ul>
            </div>
          )}
          {summary.consensus.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-emerald-800">共识（{summary.consensus.length}）</h3>
              <ul className="space-y-1.5 text-sm">
                {summary.consensus.map((c, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="mt-[7px] h-2 w-2 shrink-0 rounded-full bg-emerald-500" aria-hidden />
                    <span>
                      <span className="font-medium">{c.topic}</span>：{c.detail}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {summary.groupable.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-semibold text-sky-800">可以分组（{summary.groupable.length}）</h3>
              <ul className="space-y-1.5 text-sm">
                {summary.groupable.map((g, i) => (
                  <li key={i} className="rounded-lg border border-sky-200 bg-sky-50/60 px-3 py-2">
                    <div>
                      <span className="font-medium">{g.topic}</span>：{g.detail}
                    </div>
                    <div className="text-xs text-ink-soft">
                      想去：{g.member_ids.map((m) => names[m] ?? m).join("、") || "—"}
                      {g.others && `；其他人：${g.others}`}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {summary.decision_ids.length > 0 && (
            <p className="text-sm text-ink-soft">偏好里的 {summary.decision_ids.length} 个必玩项目已记入决策账本，猪猪改行程时会照着来；能对应到具体项目的，没排上会在「行程检查」里提示。</p>
          )}
          <Button
            onClick={() => onAsk("【意见汇总】请根据大家的偏好和最近一次汇总，看看行程有哪些地方要调整：共识的照做，有分歧的列出选项，不要替大家决定。")}
          >
            让猪猪按汇总调整行程
          </Button>
        </Section>
      )}

      <Section
        title="投票"
        hint="投票公开、可以改票。所有人投完或到截止时间自动结束，结果写进决策账本；平票由发起人拍板。"
        action={
          !draft && (
            <Button className="min-h-9 px-3 text-sm" onClick={() => setDraft({ question: "", options: [] })}>
              ＋ 发起投票
            </Button>
          )
        }
      >
        {draft && (
          <NewPoll
            key={draft.divergenceId ?? "new"}
            initial={draft}
            busy={busy === "poll"}
            onCancel={() => setDraft(null)}
            onSubmit={async (question, options, deadlineHours) => {
              const ok = await run("poll", () =>
                api.createPoll(tripId, {
                  member_id: memberId,
                  question,
                  options: options.filter((o) => o.label.trim()),
                  deadline_hours: deadlineHours,
                  divergence_id: draft.divergenceId ?? null,
                }),
              );
              if (ok) setDraft(null);
            }}
          />
        )}
        {group.polls.length === 0 && !draft && <p className="text-sm text-ink-faint">还没有投票。有分歧时可以从汇总结果里直接发起。</p>}
        <div className="space-y-3">
          {[...openPolls, ...closedPolls].map((p) => (
            <PollCard
              key={p.id}
              poll={p}
              names={names}
              memberId={memberId}
              canClose={canManage || p.created_by === memberId}
              canDecide={canManage}
              busy={busy === p.id}
              onVote={(optionId) => run(p.id, () => api.vote(tripId, p.id, memberId, optionId), true)}
              onClose={(optionId) => run(p.id, () => api.closePoll(tripId, p.id, memberId, optionId), true)}
              onAsk={onAsk}
            />
          ))}
        </div>
      </Section>

      {filledByMe && myPrefs}

      {guides && guides.length > 0 && (
        <Section title="大家读过的攻略" hint="攻略里提到的地点，在报告的项目卡片里会附上链接。">
          <div className="space-y-2">
            {guides.map((g) => (
              <GuideCard
                key={g.id}
                guide={g}
                compact
                onDelete={
                  g.added_by === memberId || canManage
                    ? () => api.deleteGuide(tripId, g.id, memberId).then(() => setGuides(guides.filter((x) => x.id !== g.id)), (e) => setActionError(errorText(e)))
                    : undefined
                }
              />
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
