"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, errorText, type BookingKind, type TripIntake } from "@/lib/api";
import { useSession } from "@/lib/session";
import { intakeDraftKey, readStorage, writeStorage } from "@/lib/storage";
import { Choice, Collapsible, MultiChips, Section, TagInput } from "./forms";
import { ProfileFields, profileErrors, profileInput, profileLine, type ProfileForm } from "./ProfileFields";
import { Button, Card, ComingSoon, ErrorBox, Field, Switch, cx, fieldClass, inputClass } from "./ui";

// ---------- 表单状态（全部用字符串，提交时再转成 TripIntake） ----------

export interface MustDoForm {
  key: string;
  what: string;
  place_hint: string;
  date: string;
  fixed_activity: boolean;
  fixed_place: boolean;
  fixed_date: boolean;
}

interface MemberForm extends ProfileForm {
  key: string;
  name: string;
  must_do: MustDoForm[];
  wishlist: string[];
  avoid: string[];
  thrill_level: "" | "low" | "mid" | "high";
  weight_kg: string;
  diet: string[];
  large_luggage: string;
  payment_note: string;
}

interface BookingForm {
  key: string;
  kind: BookingKind;
  description: string;
}

interface IntakeForm {
  destination: "NZ" | "AU";
  regions: string[];
  dateMode: "dates" | "month";
  start_date: string;
  end_date: string;
  month: string;
  days: string;
  origin_city: string;
  party: string;
  couples: [string, string][]; // 成员 key
  transport: "" | TripIntake["transport"];
  budget_amount: string;
  budget_currency: string;
  room_budget_amount: string;
  room_budget_currency: string;
  settle_currency: string;
  accommodation_type: TripIntake["accommodation_type"];
  room_requirements: string;
  need_parking: boolean;
  max_daily_drive_hours: string;
  earliest_start: string;
  pace: "" | NonNullable<TripIntake["pace"]>;
  output_format: TripIntake["output_format"];
  members: MemberForm[];
  bookings: BookingForm[];
}

let seq = 0;
const newKey = () => `k${Date.now().toString(36)}${(seq++).toString(36)}`;

export const emptyMustDo = (): MustDoForm => ({
  key: newKey(),
  what: "",
  place_hint: "",
  date: "",
  fixed_activity: true,
  fixed_place: false,
  fixed_date: false,
});

const emptyMember = (): MemberForm => ({
  key: newKey(),
  name: "",
  passport_country: "CN",
  passport_other: "",
  visa: "",
  can_drive: "",
  age: "",
  licence_country: "",
  licence_type: "",
  must_do: [],
  wishlist: [],
  avoid: [],
  thrill_level: "",
  weight_kg: "",
  diet: [],
  large_luggage: "",
  payment_note: "",
});

const initialForm = (): IntakeForm => ({
  destination: "NZ",
  regions: [],
  dateMode: "dates",
  start_date: "",
  end_date: "",
  month: "",
  days: "",
  origin_city: "",
  party: "",
  couples: [],
  transport: "",
  budget_amount: "",
  budget_currency: "AUD",
  room_budget_amount: "",
  room_budget_currency: "AUD",
  settle_currency: "AUD",
  accommodation_type: "any",
  room_requirements: "",
  need_parking: false,
  max_daily_drive_hours: "",
  earliest_start: "",
  pace: "",
  output_format: "word",
  members: [emptyMember()],
  bookings: [],
});

// ---------- 选项 ----------

const REGIONS: Record<IntakeForm["destination"], string[]> = {
  NZ: ["南岛", "北岛"],
  AU: ["维多利亚", "新南威尔士", "昆士兰", "塔斯马尼亚", "南澳", "西澳", "北领地"],
};

const CURRENCIES = ["AUD", "NZD", "CNY", "USD"];
const PARTY_PRESETS = ["朋友", "情侣", "情侣 + 朋友", "家庭"];

const KIND_LABEL: Record<BookingKind, string> = {
  flight: "机票",
  accommodation: "住宿",
  car: "租车",
  activity: "活动",
  other: "其他",
};

const STEPS = ["行程", "成员", "已有预订", "确认提交"];

// ---------- 校验与转换 ----------

function validate(form: IntakeForm, step: number): string[] {
  const errs: string[] = [];
  if (step === 0) {
    if (form.dateMode === "dates") {
      if (!form.start_date || !form.end_date) errs.push("请填写出发和返回日期，或者改成“只知道月份和天数”");
      else if (form.end_date < form.start_date) errs.push("返回日期不能早于出发日期");
    } else {
      if (!form.month) errs.push("请选择出发月份");
      const d = Number(form.days);
      if (!form.days || !Number.isInteger(d) || d < 1 || d > 60) errs.push("请填写 1–60 之间的天数");
    }
    if (!form.origin_city.trim()) errs.push("请填写出发城市");
    else if (form.origin_city.trim().length > 60) errs.push("出发城市最多 60 个字");
    if (!form.party.trim()) errs.push("请说明成员关系（例如朋友、情侣、家庭）");
    else if (form.party.trim().length > 300) errs.push("成员关系最多 300 个字");
    if (form.room_requirements.trim().length > 300) errs.push("房间和床型要求最多 300 个字");
    if (!form.transport) errs.push("请选择交通方式");
    if (!form.budget_amount.trim() && !form.room_budget_amount.trim()) errs.push("请填写每人总预算，或者住宿每间每晚的预算");
    for (const [label, v] of [
      ["每人总预算", form.budget_amount],
      ["住宿预算", form.room_budget_amount],
      ["每天最多开车小时数", form.max_daily_drive_hours],
    ] as const) {
      if (v.trim() && !(Number(v) > 0)) errs.push(`${label}要填大于 0 的数字`);
    }
  }
  if (step === 1) {
    if (!form.members.length) errs.push("至少要有一位成员");
    if (form.members.length > 12) errs.push("一个行程最多 12 位成员");
    const names = new Set<string>();
    form.members.forEach((m, i) => {
      const who = m.name.trim() || `第 ${i + 1} 位成员`;
      if (!m.name.trim()) errs.push(`第 ${i + 1} 位成员：请填写称呼`);
      else if (names.has(m.name.trim())) errs.push(`称呼“${m.name.trim()}”重复了`);
      names.add(m.name.trim());
      errs.push(...profileErrors(m, who));
      if (m.name.trim().length > 40) errs.push(`${who}：称呼最多 40 个字`);
      if (m.must_do.length > 20) errs.push(`${who}：必玩项目最多 20 个`);
      m.must_do.forEach((d, j) => {
        if (!d.what.trim()) return;
        if (d.what.trim().length > 100 || d.place_hint.trim().length > 100) errs.push(`${who} 的第 ${j + 1} 个必玩项目：最多 100 个字`);
        if (d.fixed_place && !d.place_hint.trim()) errs.push(`${who} 的第 ${j + 1} 个必玩项目：勾了“必须在这个地点”，请填写地点`);
        if (d.fixed_date && !d.date) errs.push(`${who} 的第 ${j + 1} 个必玩项目：勾了“必须在这天”，请选日期`);
      });
      if (m.weight_kg.trim() && !(Number(m.weight_kg) > 0)) errs.push(`${who}：体重要填数字`);
      if (m.large_luggage.trim() && !(Number(m.large_luggage) >= 0)) errs.push(`${who}：大箱数量要填数字`);
    });
    form.couples.forEach(([a, b], i) => {
      if (!a || !b || a === b) errs.push(`第 ${i + 1} 对情侣：请选两位不同的成员`);
    });
  }
  return errs;
}

const num = (s: string) => (s.trim() === "" ? null : Number(s));
const int = (s: string) => (s.trim() === "" ? null : Math.round(Number(s)));

/** 折叠起来的选填项里已经填了几项（折叠时也看得出来里面有内容） */
const countFilled = (values: unknown[]) => values.filter((v) => (Array.isArray(v) ? v.length > 0 : Boolean(v))).length;

const tripExtras = (f: IntakeForm) =>
  countFilled([
    f.accommodation_type !== "any",
    f.room_requirements.trim(),
    f.need_parking,
    f.max_daily_drive_hours.trim(),
    f.earliest_start,
    f.pace,
    f.settle_currency !== "AUD",
    f.output_format !== "word",
  ]);

const memberExtras = (m: MemberForm) =>
  countFilled([m.wishlist, m.avoid, m.thrill_level, m.weight_kg.trim(), m.large_luggage.trim(), m.diet, m.payment_note.trim()]);

function toIntake(form: IntakeForm): TripIntake {
  const nameOf = (key: string) => form.members.find((m) => m.key === key)?.name.trim() ?? "";
  return {
    destination: form.destination,
    regions: form.regions,
    start_date: form.dateMode === "dates" ? form.start_date || null : null,
    end_date: form.dateMode === "dates" ? form.end_date || null : null,
    month: form.dateMode === "month" ? form.month || null : null,
    days: form.dateMode === "month" ? int(form.days) : null,
    origin_city: form.origin_city.trim(),
    party: form.party.trim(),
    couples: form.couples.map(([a, b]) => [nameOf(a), nameOf(b)]).filter(([a, b]) => a && b && a !== b),
    transport: form.transport || "self_drive",
    budget_per_person: form.budget_amount.trim()
      ? { amount: Number(form.budget_amount), currency: form.budget_currency, per: "person" }
      : null,
    room_budget_per_night: form.room_budget_amount.trim()
      ? { amount: Number(form.room_budget_amount), currency: form.room_budget_currency, per: "group" }
      : null,
    settle_currency: form.settle_currency,
    accommodation_type: form.accommodation_type,
    room_requirements: form.room_requirements.trim(),
    need_parking: form.need_parking,
    max_daily_drive_hours: num(form.max_daily_drive_hours),
    earliest_start: form.earliest_start || null,
    pace: form.pace || null,
    existing_bookings: form.bookings
      .filter((b) => b.description.trim())
      .map((b) => ({ kind: b.kind, description: b.description.trim() })),
    output_format: form.output_format,
    members: form.members.map((m) => ({
      name: m.name.trim(),
      ...profileInput(m),
      must_do: m.must_do
        .filter((d) => d.what.trim())
        .map((d) => ({
          what: d.what.trim(),
          place_hint: d.place_hint.trim() || null,
          date: d.date || null,
          fixed_activity: d.fixed_activity,
          fixed_place: d.fixed_place,
          fixed_date: d.fixed_date,
        })),
      wishlist: m.wishlist,
      avoid: m.avoid,
      thrill_level: m.thrill_level || null,
      weight_kg: num(m.weight_kg),
      diet: m.diet,
      large_luggage: int(m.large_luggage),
      payment_note: m.payment_note.trim() || null,
    })),
  };
}

// ---------- 各步骤 ----------

type Update = (patch: Partial<IntakeForm>) => void;

function TripStep({ form, update }: { form: IntakeForm; update: Update }) {
  return (
    <div className="space-y-4">
      <Section title="去哪里、什么时候" hint="带 * 的是必填项，其余可以跳过，没填的会用默认值并在计划里列出来。">
        <Field group label="目的地" required>
          <Choice
            value={form.destination}
            onChange={(v) => update({ destination: v, regions: form.regions.filter((r) => REGIONS[v].includes(r)) })}
            options={[
              { value: "NZ", label: "新西兰" },
              { value: "AU", label: "澳大利亚" },
            ]}
          />
        </Field>
        <div>
          <div className="mb-1 text-sm font-medium">区域</div>
          <MultiChips value={form.regions} onChange={(v) => update({ regions: v })} options={REGIONS[form.destination]} />
          <p className="mt-1 text-xs text-ink-faint">{form.regions.length ? `已选：${form.regions.join("、")}` : "都不选表示“请推荐”"}</p>
        </div>
        <Field group label="日期" required>
          <Choice
            value={form.dateMode}
            onChange={(v) => update({ dateMode: v })}
            options={[
              { value: "dates", label: "日期已经定了" },
              { value: "month", label: "只知道月份和天数", hint: "由猪猪推荐具体日期" },
            ]}
          />
        </Field>
        {form.dateMode === "dates" ? (
          <div className="grid grid-cols-2 gap-3">
            <Field label="出发日期" required>
              <input type="date" className={inputClass} value={form.start_date} onChange={(e) => update({ start_date: e.target.value })} />
            </Field>
            <Field label="返回日期" required>
              <input
                type="date"
                className={inputClass}
                value={form.end_date}
                min={form.start_date || undefined}
                onChange={(e) => update({ end_date: e.target.value })}
              />
            </Field>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-3">
            <Field label="月份" required>
              <input type="month" className={inputClass} value={form.month} onChange={(e) => update({ month: e.target.value })} />
            </Field>
            <Field label="天数" required>
              <input
                type="number"
                inputMode="numeric"
                min={1}
                max={60}
                className={inputClass}
                value={form.days}
                onChange={(e) => update({ days: e.target.value })}
                placeholder="例如 10"
              />
            </Field>
          </div>
        )}
        <Field label="出发城市" required hint="影响机票和开口程建议">
          <input
            className={inputClass}
            value={form.origin_city}
            onChange={(e) => update({ origin_city: e.target.value })}
            placeholder="例如 墨尔本"
            list="origin-cities"
          />
          <datalist id="origin-cities">
            {["墨尔本", "悉尼", "布里斯班", "阿德莱德", "珀斯", "堪培拉"].map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </Field>
      </Section>

      <Section title="谁去、怎么玩">
        <Field group label="成员关系" required hint="决定房间怎么分，人数按下一步填的成员算">
          <input
            className={inputClass}
            value={form.party}
            onChange={(e) => update({ party: e.target.value })}
            placeholder="例如 两对情侣 / 四个朋友"
          />
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {PARTY_PRESETS.map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => update({ party: p })}
                className="min-h-8 rounded-full border border-line px-2.5 text-sm text-ink-soft hover:border-accent-line hover:text-accent"
              >
                {p}
              </button>
            ))}
          </div>
        </Field>
        <Field group label="交通方式" required>
          <Choice
            value={form.transport}
            onChange={(v) => update({ transport: v })}
            columns={4}
            options={[
              { value: "self_drive", label: "自驾" },
              { value: "public", label: "公共交通" },
              { value: "tour", label: "跟团" },
              { value: "mixed", label: "混合" },
            ]}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="每人总预算" required={!form.room_budget_amount} hint="和住宿预算至少填一个">
            <div className="flex gap-2">
              <input
                type="number"
                inputMode="decimal"
                className={cx(inputClass, "min-w-0")}
                value={form.budget_amount}
                onChange={(e) => update({ budget_amount: e.target.value })}
                placeholder="例如 3000"
              />
              <select className={cx(fieldClass, "w-24 shrink-0")} value={form.budget_currency} onChange={(e) => update({ budget_currency: e.target.value })}>
                {CURRENCIES.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </div>
          </Field>
          <Field label="住宿每间每晚预算" required={!form.budget_amount}>
            <div className="flex gap-2">
              <input
                type="number"
                inputMode="decimal"
                className={cx(inputClass, "min-w-0")}
                value={form.room_budget_amount}
                onChange={(e) => update({ room_budget_amount: e.target.value })}
                placeholder="例如 200"
              />
              <select
                className={cx(fieldClass, "w-24 shrink-0")}
                value={form.room_budget_currency}
                onChange={(e) => update({ room_budget_currency: e.target.value })}
              >
                {CURRENCIES.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </div>
          </Field>
        </div>
      </Section>

      <Collapsible
        title="更多偏好（选填）"
        summary="住宿和床型、停车、开车时长、出发时间、节奏、AA 币种。填了第一版更合心意"
        filled={tripExtras(form)}
      >
        <Field group label="住宿类型">
          <Choice
            value={form.accommodation_type}
            onChange={(v) => update({ accommodation_type: v })}
            columns={3}
            options={[
              { value: "whole_home", label: "整套民宿" },
              { value: "hotel", label: "酒店" },
              { value: "any", label: "都可以" },
            ]}
          />
        </Field>
        <Field label="房间和床型要求">
          <input
            className={inputClass}
            value={form.room_requirements}
            onChange={(e) => update({ room_requirements: e.target.value })}
            placeholder="例如 情侣一间，另外两人必须分床"
          />
        </Field>
        <Switch checked={form.need_parking} onChange={(v) => update({ need_parking: v })} label="住宿需要停车位" />
        <div className="grid grid-cols-2 gap-3">
          <Field label="每天最多开车（小时）" hint="不填默认 5 小时">
            <input
              type="number"
              inputMode="decimal"
              step="0.5"
              className={inputClass}
              value={form.max_daily_drive_hours}
              onChange={(e) => update({ max_daily_drive_hours: e.target.value })}
            />
          </Field>
          <Field label="最早几点出发" hint="不填默认 7:00 以后">
            <input type="time" className={inputClass} value={form.earliest_start} onChange={(e) => update({ earliest_start: e.target.value })} />
          </Field>
        </div>
        <Field group label="旅行节奏">
          <Choice
            value={form.pace}
            onChange={(v) => update({ pace: v })}
            columns={3}
            options={[
              { value: "relaxed", label: "悠闲" },
              { value: "balanced", label: "适中" },
              { value: "packed", label: "排满" },
            ]}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="AA 结算币种">
            <select className={inputClass} value={form.settle_currency} onChange={(e) => update({ settle_currency: e.target.value })}>
              {CURRENCIES.map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </Field>
          <Field label="报告格式">
            <select
              className={inputClass}
              value={form.output_format}
              onChange={(e) => update({ output_format: e.target.value as IntakeForm["output_format"] })}
            >
              <option value="word">Word</option>
              <option value="pdf">PDF</option>
            </select>
          </Field>
        </div>
      </Collapsible>
    </div>
  );
}

export function MustDoEditor({ item, onChange, onRemove }: { item: MustDoForm; onChange: (v: MustDoForm) => void; onRemove: () => void }) {
  return (
    <div className="space-y-3 rounded-lg border border-line bg-canvas/60 p-3">
      <div className="flex items-end gap-2">
        <Field label="项目" className="flex-1">
          <input className={inputClass} value={item.what} onChange={(e) => onChange({ ...item, what: e.target.value })} placeholder="例如 骑马" />
        </Field>
        <Button variant="ghost" onClick={onRemove} aria-label="删除这个必玩项目">
          删除
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="地点">
          <input
            className={inputClass}
            value={item.place_hint}
            onChange={(e) => onChange({ ...item, place_hint: e.target.value })}
            placeholder="例如 格林诺奇"
          />
        </Field>
        <Field label="日期">
          <input type="date" className={inputClass} value={item.date} onChange={(e) => onChange({ ...item, date: e.target.value })} />
        </Field>
      </div>
      <div className="divide-y divide-line rounded-lg bg-white px-2">
        <Switch checked={item.fixed_activity} onChange={(v) => onChange({ ...item, fixed_activity: v })} label="必须玩这个项目" />
        <Switch checked={item.fixed_place} onChange={(v) => onChange({ ...item, fixed_place: v })} label="必须在这个地点" />
        <Switch checked={item.fixed_date} onChange={(v) => onChange({ ...item, fixed_date: v })} label="必须在这天" />
      </div>
      <p className="text-xs text-ink-faint">猪猪找替代方案时，只会改动没勾选的那几项。</p>
    </div>
  );
}

function MemberEditor({
  index,
  member,
  canRemove,
  onChange,
  onRemove,
}: {
  index: number;
  member: MemberForm;
  canRemove: boolean;
  onChange: (m: MemberForm) => void;
  onRemove: () => void;
}) {
  const set = (patch: Partial<MemberForm>) => onChange({ ...member, ...patch });
  return (
    <Section
      title={member.name.trim() || `成员 ${index + 1}`}
      action={
        canRemove && (
          <Button variant="ghost" className="min-h-9 text-sm text-red-700" onClick={onRemove}>
            移除
          </Button>
        )
      }
    >
      <ProfileFields
        value={member}
        onChange={set}
        idKey={member.key}
        lead={
          <Field label="称呼" required hint={index === 0 ? "第一位填你自己，你就是发起人" : "用于分组、待办负责人"}>
            <input className={inputClass} value={member.name} onChange={(e) => set({ name: e.target.value })} placeholder="例如 Nina" />
          </Field>
        }
      />

      <div className="space-y-2">
        <div>
          <div className="text-sm font-medium">必玩项目</div>
          <p className="text-xs text-ink-faint">每项可以标三个开关：必须玩这个项目 / 必须在这个地点 / 必须在这天。</p>
        </div>
        {member.must_do.map((d, j) => (
          <MustDoEditor
            key={d.key}
            item={d}
            onChange={(v) => set({ must_do: member.must_do.map((x, k) => (k === j ? v : x)) })}
            onRemove={() => set({ must_do: member.must_do.filter((_, k) => k !== j) })}
          />
        ))}
        <Button onClick={() => set({ must_do: [...member.must_do, emptyMustDo()] })} className="w-full sm:w-auto">
          ＋ 添加必玩项目
        </Button>
      </div>

      <Collapsible
        title="更多（选填）"
        summary="想去的、不敢玩的、刺激程度、饮食、体重、行李箱等。猪猪挑项目、选车型都用得上"
        filled={memberExtras(member)}
      >
        <Field group label="想去的地方" hint="可以贴小红书链接">
          <TagInput value={member.wishlist} onChange={(v) => set({ wishlist: v })} placeholder="输入后回车" />
        </Field>
        <Field group label="不想玩或不敢玩的">
          <TagInput value={member.avoid} onChange={(v) => set({ avoid: v })} placeholder="输入后回车" suggestions={["怕高", "怕水", "晕车", "晕船"]} />
        </Field>
        <Field group label="刺激程度接受度">
          <Choice
            value={member.thrill_level}
            onChange={(v) => set({ thrill_level: v })}
            columns={3}
            options={[
              { value: "low", label: "低" },
              { value: "mid", label: "中" },
              { value: "high", label: "高" },
            ]}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="体重（kg）" hint="只用于骑马、漂流等限制提醒，不给其他成员看">
            <input
              type="number"
              inputMode="decimal"
              className={inputClass}
              value={member.weight_kg}
              onChange={(e) => set({ weight_kg: e.target.value })}
            />
          </Field>
          <Field label="大行李箱（个）" hint="决定租什么车">
            <input
              type="number"
              inputMode="numeric"
              min={0}
              className={inputClass}
              value={member.large_luggage}
              onChange={(e) => set({ large_luggage: e.target.value })}
            />
          </Field>
        </div>
        <Field group label="饮食要求">
          <TagInput maxItems={20} value={member.diet} onChange={(v) => set({ diet: v })} placeholder="输入后回车" suggestions={["素食", "不吃辣", "海鲜过敏"]} />
        </Field>
        <Field label="收款方式备注" hint="只在结算页给同行成员看">
          <input
            className={inputClass}
            value={member.payment_note}
            onChange={(e) => set({ payment_note: e.target.value })}
            placeholder="例如 PayID 或微信"
          />
        </Field>
      </Collapsible>
    </Section>
  );
}

function MembersStep({ form, update }: { form: IntakeForm; update: Update }) {
  const named = form.members.filter((m) => m.name.trim());
  return (
    <div className="space-y-4">
      <p className="px-1 text-sm text-ink-soft">朋友的资料先按你知道的填，TA 们用专属链接加入后可以自己改。</p>
      {form.members.map((m, i) => (
        <MemberEditor
          key={m.key}
          index={i}
          member={m}
          canRemove={form.members.length > 1}
          onChange={(nm) => update({ members: form.members.map((x) => (x.key === m.key ? nm : x)) })}
          onRemove={() =>
            update({
              members: form.members.filter((x) => x.key !== m.key),
              couples: form.couples.filter(([a, b]) => a !== m.key && b !== m.key),
            })
          }
        />
      ))}
      <Button onClick={() => update({ members: [...form.members, emptyMember()] })} className="w-full">
        ＋ 添加成员
      </Button>

      {form.members.length >= 2 && (
        <Section title="情侣（同住一间）" hint="选填。没填的话默认情侣一间、其他人各一张床。">
          {form.couples.map(([a, b], i) => (
            <div key={i} className="flex items-center gap-2">
              {[a, b].map((v, side) => (
                <select
                  key={side}
                  className={inputClass}
                  value={v}
                  onChange={(e) =>
                    update({
                      couples: form.couples.map((c, k) =>
                        k === i ? ((side === 0 ? [e.target.value, c[1]] : [c[0], e.target.value]) as [string, string]) : c,
                      ),
                    })
                  }
                >
                  <option value="">选择成员</option>
                  {named.map((m) => (
                    <option key={m.key} value={m.key}>
                      {m.name}
                    </option>
                  ))}
                </select>
              ))}
              <Button variant="ghost" onClick={() => update({ couples: form.couples.filter((_, k) => k !== i) })}>
                删除
              </Button>
            </div>
          ))}
          <Button onClick={() => update({ couples: [...form.couples, ["", ""]] })} disabled={named.length < 2}>
            ＋ 添加一对
          </Button>
        </Section>
      )}
    </div>
  );
}

function BookingsStep({ form, update }: { form: IntakeForm; update: Update }) {
  return (
    <Section title="已经订好的" hint="选填。机票、住宿、活动已经订了的，写清楚时间和名称，排行程时会锁定不动。之后也可以在行程页上传确认单。">
      {form.bookings.length === 0 && <p className="text-sm text-ink-faint">还没有添加。</p>}
      {form.bookings.map((b, i) => (
        <div key={b.key} className="space-y-2 rounded-lg border border-line bg-canvas/60 p-3">
          <div className="flex items-center gap-2">
            <select
              className={cx(fieldClass, "w-28 shrink-0")}
              value={b.kind}
              onChange={(e) =>
                update({ bookings: form.bookings.map((x, k) => (k === i ? { ...x, kind: e.target.value as BookingKind } : x)) })
              }
              aria-label="类型"
            >
              {(Object.keys(KIND_LABEL) as BookingKind[]).map((k) => (
                <option key={k} value={k}>
                  {KIND_LABEL[k]}
                </option>
              ))}
            </select>
            <div className="flex-1" />
            <Button variant="ghost" onClick={() => update({ bookings: form.bookings.filter((_, k) => k !== i) })}>
              删除
            </Button>
          </div>
          <textarea
            className={cx(inputClass, "min-h-20")}
            maxLength={500}
            value={b.description}
            onChange={(e) => update({ bookings: form.bookings.map((x, k) => (k === i ? { ...x, description: e.target.value } : x)) })}
            placeholder="例如 QF177 11/23 06:40 墨尔本 → 皇后镇，4 人"
            aria-label="预订内容"
          />
        </div>
      ))}
      <Button
        onClick={() => update({ bookings: [...form.bookings, { key: newKey(), kind: "flight", description: "" }] })}
        className="w-full sm:w-auto"
      >
        ＋ 添加预订
      </Button>
    </Section>
  );
}

const TRANSPORT_LABEL = { self_drive: "自驾", public: "公共交通", tour: "跟团", mixed: "混合" };
const PACE_LABEL = { relaxed: "悠闲", balanced: "适中", packed: "排满" };
const ACCOM_LABEL = { whole_home: "整套民宿", hotel: "酒店", any: "都可以" };

function ReviewStep({ form }: { form: IntakeForm }) {
  const it = toIntake(form);
  const rows: [string, string][] = [
    ["目的地", `${it.destination === "NZ" ? "新西兰" : "澳大利亚"}${it.regions.length ? `（${it.regions.join("、")}）` : "（区域请推荐）"}`],
    ["日期", it.start_date ? `${it.start_date} 至 ${it.end_date}` : `${it.month}，${it.days} 天`],
    ["出发城市", it.origin_city],
    ["成员关系", `${it.party}，共 ${it.members.length} 人`],
    ["交通", TRANSPORT_LABEL[it.transport]],
    [
      "预算",
      [
        it.budget_per_person && `每人 ${it.budget_per_person.amount} ${it.budget_per_person.currency}`,
        it.room_budget_per_night && `住宿每间每晚 ${it.room_budget_per_night.amount} ${it.room_budget_per_night.currency}`,
      ]
        .filter(Boolean)
        .join("；"),
    ],
    ["住宿", `${ACCOM_LABEL[it.accommodation_type]}${it.room_requirements ? `；${it.room_requirements}` : ""}${it.need_parking ? "；需要停车" : ""}`],
    ["节奏", `${it.pace ? PACE_LABEL[it.pace] : "默认"}；每天最多开车 ${it.max_daily_drive_hours ?? "5（默认）"} 小时；${it.earliest_start ?? "7:00（默认）"} 以后出发`],
  ];
  return (
    <div className="space-y-4">
      <Section title="行程">
        <dl className="divide-y divide-line/70">
          {rows.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[5em_minmax(0,1fr)] gap-3 py-2 text-[15px]">
              <dt className="text-ink-faint">{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      </Section>
      <Section title={`成员（${it.members.length}）`}>
        <ul className="space-y-3">
          {it.members.map((m, i) => (
            <li key={i} className="text-[15px]">
              <div className="font-medium">{m.name}</div>
              <div className="text-sm text-ink-soft">{profileLine(m)}</div>
              {m.must_do.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-sm">
                  {m.must_do.map((d, i) => (
                    <li key={i}>
                      必玩：{d.what}
                      {d.place_hint ? ` @ ${d.place_hint}` : ""}
                      {d.date ? ` · ${d.date}` : ""}
                      <span className="text-ink-faint">
                        {" "}
                        （{[d.fixed_activity && "项目固定", d.fixed_place && "地点固定", d.fixed_date && "日期固定"].filter(Boolean).join("、") || "都可以换"}）
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
        {it.couples.length > 0 && <p className="text-sm text-ink-soft">情侣：{it.couples.map((c) => c.join(" & ")).join("；")}</p>}
      </Section>
      <Section title={`已有预订（${it.existing_bookings.length}）`}>
        {it.existing_bookings.length ? (
          <ul className="space-y-1 text-[15px]">
            {it.existing_bookings.map((b, i) => (
              <li key={i}>
                <span className="text-ink-faint">{KIND_LABEL[b.kind]}：</span>
                {b.description}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-faint">没有</p>
        )}
      </Section>
    </div>
  );
}

// ---------- 主体 ----------

export function IntakeWizard() {
  const router = useRouter();
  const [form, setForm] = useState<IntakeForm>(initialForm);
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [copied, setCopied] = useState(false);
  const [restored, setRestored] = useState(false);
  const [betaCode, setBetaCode] = useState("");
  const session = useSession();
  const topRef = useRef<HTMLDivElement>(null);
  const feedbackRef = useRef<HTMLDivElement>(null);

  // 校验没通过、提交出错时，把提示滚到看得见的地方
  useEffect(() => {
    if (errors.length || submitError || unavailable) feedbackRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [errors, submitError, unavailable]);

  // 草稿保存在这台设备上，刷新不会丢
  useEffect(() => {
    const raw = readStorage(intakeDraftKey);
    if (raw) {
      try {
        const saved = JSON.parse(raw) as { form?: IntakeForm; step?: number };
        if (saved.form && Array.isArray(saved.form.members)) {
          setForm({ ...initialForm(), ...saved.form });
          setStep(Math.min(Math.max(saved.step ?? 0, 0), STEPS.length - 1));
        }
      } catch {
        // 草稿坏了就不用
      }
    }
    setRestored(true);
  }, []);

  useEffect(() => {
    // 读完草稿之后才开始保存，避免先用空表单把草稿覆盖掉
    if (restored) writeStorage(intakeDraftKey, JSON.stringify({ form, step }));
  }, [form, step, restored]);

  const update: Update = (patch) => {
    setForm((f) => ({ ...f, ...patch }));
    if (errors.length) setErrors([]);
  };

  function go(next: number) {
    if (next > step) {
      for (let s = step; s < next; s++) {
        const errs = validate(form, s);
        if (errs.length) {
          setStep(s);
          setErrors(errs);
          return;
        }
      }
    }
    setErrors([]);
    setStep(next);
    topRef.current?.scrollIntoView({ block: "start" });
  }

  async function submit() {
    const errs = [...validate(form, 0), ...validate(form, 1)];
    if (errs.length) {
      setErrors(errs);
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    setUnavailable(false);
    try {
      const { job_id } = await api.createTrip(toIntake(form), betaCode.trim() || undefined);
      writeStorage(intakeDraftKey, null);
      router.push(`/jobs/${encodeURIComponent(job_id)}`);
    } catch (e) {
      if (e instanceof ApiError && e.notAvailable) setUnavailable(true);
      else setSubmitError(errorText(e));
      setSubmitting(false);
    }
  }

  async function copyJson() {
    try {
      await navigator.clipboard.writeText(JSON.stringify(toIntake(form), null, 2));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  function reset() {
    if (!window.confirm("清空已经填写的内容？")) return;
    setForm(initialForm());
    setStep(0);
    setErrors([]);
  }

  return (
    <div ref={topRef} className="mx-auto max-w-2xl scroll-mt-4 px-4 pt-6 pb-28 sm:pt-10">
      <div className="flex items-center justify-between">
        <Link href="/" className="text-sm text-ink-soft hover:text-ink">
          ← 我的行程
        </Link>
        <button type="button" onClick={reset} className="text-sm text-ink-faint hover:text-red-700">
          清空问卷
        </button>
      </div>
      <h1 className="mt-2 text-2xl font-semibold">新建行程</h1>
      <p className="mt-1 text-ink-soft">先填必填项，选填项可以跳过。填写内容会自动保存在这台设备上。</p>

      <ol className="mt-5 mb-5 grid grid-cols-4 gap-1.5">
        {STEPS.map((s, i) => (
          <li key={s}>
            <button
              type="button"
              onClick={() => go(i)}
              className={cx("w-full text-left", i > step && "cursor-pointer")}
              aria-current={i === step ? "step" : undefined}
            >
              <div className={cx("h-1.5 rounded-full", i <= step ? "bg-accent" : "bg-gray-200")} />
              <div className={cx("mt-1 truncate text-xs sm:text-sm", i === step ? "font-semibold text-accent" : "text-ink-faint")}>
                {i + 1}. {s}
              </div>
            </button>
          </li>
        ))}
      </ol>

      {step === 0 && <TripStep form={form} update={update} />}
      {step === 1 && <MembersStep form={form} update={update} />}
      {step === 2 && <BookingsStep form={form} update={update} />}
      {step === 3 && <ReviewStep form={form} />}
      {step === 3 && session?.needs_beta_code && (
        <Card className="mt-4 p-4">
          <Field label="内测邀请码" required hint="小猪游记还在内测，生成行程会调用模型，需要邀请码">
            <input className={inputClass} value={betaCode} onChange={(e) => setBetaCode(e.target.value)} autoComplete="off" />
          </Field>
        </Card>
      )}

      <div ref={feedbackRef} />
      {errors.length > 0 && (
        <div role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[15px] text-red-800">
          <div className="font-medium">还有几项要补充：</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {errors.map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {step === 3 && submitError && (
        <div className="mt-4">
          <ErrorBox>提交失败：{submitError}</ErrorBox>
        </div>
      )}
      {step === 3 && unavailable && (
        <div className="mt-4 space-y-2">
          <ComingSoon title="新建行程功能即将上线">
            后端还没开放生成接口。问卷已经保存在这台设备上，上线后回到这里直接提交就行。
          </ComingSoon>
          <Button onClick={copyJson}>{copied ? "已复制" : "复制问卷 JSON"}</Button>
        </div>
      )}

      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-2xl gap-2 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          {step > 0 && (
            <Button onClick={() => go(step - 1)} className="flex-1 sm:flex-none sm:px-6">
              上一步
            </Button>
          )}
          {step < STEPS.length - 1 ? (
            <Button variant="primary" onClick={() => go(step + 1)} className="flex-[2] sm:ml-auto sm:flex-none sm:px-8">
              下一步
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={submit}
              busy={submitting}
              disabled={submitting}
              className="flex-[2] sm:ml-auto sm:flex-none sm:px-8"
            >
              提交，开始生成
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
