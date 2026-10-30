"use client";

import type { ReactNode } from "react";
import type { Member, MemberProfile } from "@/lib/api";
import { Choice } from "./forms";
import { Field, inputClass } from "./ui";

// 成员资料：护照、签证、能不能开车、年龄、驾照。建行程的问卷和成员自己改资料用同一套。

export type YesNo = "" | "yes" | "no";

/** 表单状态（全部用字符串，保存时再转换） */
export interface ProfileForm {
  passport_country: string;
  passport_other: string;
  visa: string;
  can_drive: YesNo;
  age: string;
  licence_country: string;
  licence_type: string;
}

export const PASSPORTS = [
  { value: "CN", label: "中国" },
  { value: "HK", label: "中国香港" },
  { value: "MO", label: "中国澳门" },
  { value: "TW", label: "中国台湾" },
  { value: "AU", label: "澳大利亚" },
  { value: "NZ", label: "新西兰" },
  { value: "MY", label: "马来西亚" },
  { value: "SG", label: "新加坡" },
  { value: "OTHER", label: "其他" },
];

const VISAS = ["澳洲 500 学生签", "澳洲 485 毕业生签", "澳洲永居（PR）", "澳洲公民"];

const LICENCE_COUNTRIES = [
  { value: "AU", label: "澳洲驾照" },
  { value: "CN", label: "中国驾照" },
  { value: "NZ", label: "新西兰驾照" },
  { value: "OTHER", label: "其他" },
];

const LICENCE_TYPES = [
  { value: "full", label: "正式驾照（Full）" },
  { value: "p", label: "P 牌" },
  { value: "learner", label: "L 牌（学习）" },
  { value: "other", label: "其他" },
];

export const LICENCE_COUNTRY: Record<string, string> = { AU: "澳洲驾照", CN: "中国驾照", NZ: "新西兰驾照", OTHER: "其他国家驾照" };
export const LICENCE_TYPE: Record<string, string> = { full: "正式", p: " P 牌", provisional: " P 牌", learner: " L 牌", other: "" };

const KNOWN_PASSPORTS = new Set(PASSPORTS.map((p) => p.value).filter((v) => v !== "OTHER"));

/** 行程里的成员资料 → 表单 */
export function profileForm(m: Member): ProfileForm {
  const passport = m.passport_country ?? "";
  const known = !passport || KNOWN_PASSPORTS.has(passport);
  return {
    passport_country: known ? passport : "OTHER",
    passport_other: known ? "" : passport,
    visa: m.visa ?? "",
    can_drive: m.can_drive ? "yes" : "no",
    age: m.age != null ? String(m.age) : "",
    licence_country: m.licence_country ?? "",
    licence_type: m.licence_type ?? "",
  };
}

/** 没填好的地方。who 是提示前面的称呼（问卷里有好几个人）；改自己的资料时不用。 */
export function profileErrors(p: ProfileForm, who?: string): string[] {
  const errs: string[] = [];
  const add = (text: string) => errs.push(who ? `${who}：${text}` : text);
  if (!p.passport_country) add("请选择护照国籍");
  if (p.passport_country === "OTHER" && !p.passport_other.trim()) add("请填写护照国籍");
  if (p.passport_other.trim().length > 60) add("护照国籍最多 60 个字");
  if (p.visa.trim().length > 100) add("签证最多 100 个字");
  if (!p.can_drive) add("请选择能不能开车");
  if (p.can_drive === "yes") {
    const age = Number(p.age);
    if (!p.age || !Number.isInteger(age) || age < 16 || age > 99) add("能开车的话请填写年龄");
    if (!p.licence_country) add("请选择驾照国家");
    if (!p.licence_type) add("请选择驾照类型");
  }
  return errs;
}

/** 表单 → 提交的资料。不开车的人不留年龄和驾照（和建行程时一样）。 */
export function profileInput(p: ProfileForm) {
  const drives = p.can_drive === "yes";
  return {
    passport_country: p.passport_country === "OTHER" ? p.passport_other.trim() : p.passport_country,
    visa: p.visa.trim() || null,
    can_drive: drives,
    age: drives && p.age.trim() ? Math.round(Number(p.age)) : null,
    licence_country: drives ? p.licence_country || null : null,
    licence_type: drives ? p.licence_type || null : null,
  } satisfies MemberProfile;
}

/** 一行说明，例如"中国护照 · 澳洲 500 学生签 · 能开车（24 岁，中国驾照正式）" */
export function profileLine(m: MemberProfile): string {
  const passport = m.passport_country
    ? `${PASSPORTS.find((p) => p.value === m.passport_country)?.label ?? m.passport_country} 护照`
    : "护照未填";
  const drive = m.can_drive
    ? `能开车（${m.age ?? "?"} 岁，${LICENCE_COUNTRY[m.licence_country ?? ""] ?? m.licence_country ?? "驾照未填"}${LICENCE_TYPE[m.licence_type ?? ""] ?? m.licence_type ?? ""}）`
    : "不开车";
  return `${passport}${m.visa ? ` · ${m.visa}` : ""} · ${drive}`;
}

/** 还缺的资料（用来提醒本人补上） */
export function profileGaps(m: Member): string[] {
  const gaps: string[] = [];
  if (!m.passport_country) gaps.push("护照国籍");
  else if (!m.visa && !["AU", "NZ"].includes(m.passport_country)) gaps.push("签证");
  if (m.can_drive) {
    if (m.age == null) gaps.push("年龄");
    if (!m.licence_country) gaps.push("驾照国家");
    if (!m.licence_type) gaps.push("驾照类型");
  }
  return gaps;
}

/** 选项里没有的旧值（例如别处写进来的 GB 驾照）也要能显示，不能悄悄换掉 */
function withCurrent(options: { value: string; label: string }[], value: string) {
  return value && !options.some((o) => o.value === value) ? [...options, { value, label: value }] : options;
}

export function ProfileFields({
  value,
  onChange,
  idKey,
  lead,
}: {
  value: ProfileForm;
  onChange: (patch: Partial<ProfileForm>) => void;
  idKey: string; // 签证候选列表的 id 要唯一（问卷里有好几个成员）
  lead?: ReactNode; // 放在最前面的输入框（问卷里的"称呼"）
}) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        {lead}
        <Field label="护照国籍" required hint="不收集护照号">
          <select className={inputClass} value={value.passport_country} onChange={(e) => onChange({ passport_country: e.target.value })}>
            {!value.passport_country && <option value="">请选择</option>}
            {PASSPORTS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
        {value.passport_country === "OTHER" && (
          <Field label="护照国籍（其他）" required>
            <input className={inputClass} value={value.passport_other} onChange={(e) => onChange({ passport_other: e.target.value })} />
          </Field>
        )}
        <Field label="当前签证" hint="用来判断入境要办什么">
          <input
            className={inputClass}
            value={value.visa}
            onChange={(e) => onChange({ visa: e.target.value })}
            placeholder="例如 澳洲 500 学生签"
            list={`visa-${idKey}`}
          />
          <datalist id={`visa-${idKey}`}>
            {VISAS.map((v) => (
              <option key={v} value={v} />
            ))}
          </datalist>
        </Field>
      </div>

      <Field group label="能不能开车" required>
        <Choice
          value={value.can_drive}
          onChange={(v) => onChange({ can_drive: v })}
          options={[
            { value: "yes", label: "能开" },
            { value: "no", label: "不开" },
          ]}
        />
      </Field>
      {value.can_drive === "yes" && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <Field label="年龄" required hint="25 岁以下租车可能加价">
            <input type="number" inputMode="numeric" className={inputClass} value={value.age} onChange={(e) => onChange({ age: e.target.value })} />
          </Field>
          <Field label="驾照国家" required>
            <select className={inputClass} value={value.licence_country} onChange={(e) => onChange({ licence_country: e.target.value })}>
              <option value="">请选择</option>
              {withCurrent(LICENCE_COUNTRIES, value.licence_country).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="驾照类型" required className="col-span-2 sm:col-span-1">
            <select className={inputClass} value={value.licence_type} onChange={(e) => onChange({ licence_type: e.target.value })}>
              <option value="">请选择</option>
              {withCurrent(LICENCE_TYPES, value.licence_type).map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        </div>
      )}
    </>
  );
}
