"use client";

import { useState } from "react";
import { api, errorText, type Member, type ProfileResult } from "@/lib/api";
import { Section } from "./forms";
import { ProfileFields, profileErrors, profileForm, profileGaps, profileInput, profileLine, type ProfileForm } from "./ProfileFields";
import { Button, ErrorBox, Field, inputClass } from "./ui";

interface State extends ProfileForm {
  weight_kg: string;
}

const toState = (m: Member): State => ({ ...profileForm(m), weight_kg: m.weight_kg != null ? String(m.weight_kg) : "" });

/**
 * 我的资料：护照、签证、开车、驾照、年龄、体重。建行程时发起人先帮大家填，加入后以本人为准。
 * 只能改自己的；改了直接生成新版本，驾照翻译件、NZeTA 这些待办和行程检查跟着更新。
 */
export function MyProfile({ tripId, member, onSaved }: { tripId: string; member: Member; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [state, setState] = useState<State>(() => toState(member));
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [result, setResult] = useState<ProfileResult | null>(null);

  const gaps = profileGaps(member);
  const set = (patch: Partial<State>) => {
    setState((s) => ({ ...s, ...patch }));
    setErrors([]);
  };

  function startEdit() {
    setState(toState(member));
    setErrors([]);
    setSaveError(null);
    setResult(null);
    setEditing(true);
  }

  async function save() {
    const errs = profileErrors(state);
    const weight = Number(state.weight_kg);
    if (state.weight_kg.trim() && !(weight > 0 && weight <= 500)) errs.push("体重要填 kg 数字");
    if (errs.length) {
      setErrors(errs);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const weightChanged = state.weight_kg !== toState(member).weight_kg;
      const res = await api.saveProfile(tripId, member.id, {
        ...profileInput(state),
        ...(weightChanged ? { weight_kg: state.weight_kg.trim() ? weight : null } : {}),
      });
      setResult(res);
      setEditing(false);
      if (res.changed) onSaved();
    } catch (e) {
      setSaveError(errorText(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Section
      title="我的资料"
      hint="建行程时发起人先帮你填了，以你本人为准。改了马上生效，驾照翻译件、NZeTA 这些待办会跟着更新。"
      action={
        !editing && (
          <Button variant={gaps.length ? "primary" : "secondary"} className="min-h-9 px-3 text-sm" onClick={startEdit}>
            {gaps.length ? "补充资料" : "修改"}
          </Button>
        )
      }
    >
      {editing ? (
        <div className="space-y-4">
          <ProfileFields value={state} onChange={set} idKey={`me-${member.id}`} />
          <Field label="体重（kg，选填）" hint="只用于骑马、漂流等项目的限制提醒，不给其他成员看" className="sm:max-w-[50%] sm:pr-1.5">
            <input
              type="number"
              inputMode="decimal"
              className={inputClass}
              value={state.weight_kg}
              onChange={(e) => set({ weight_kg: e.target.value })}
            />
          </Field>
          {errors.length > 0 && (
            <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              <ul className="list-disc space-y-0.5 pl-5">
                {errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          {saveError && <ErrorBox>没有保存：{saveError}</ErrorBox>}
          <div className="flex gap-2">
            <Button variant="primary" busy={saving} disabled={saving} onClick={save}>
              {saving ? "正在保存…" : "保存我的资料"}
            </Button>
            <Button variant="ghost" disabled={saving} onClick={() => setEditing(false)}>
              取消
            </Button>
          </div>
        </div>
      ) : (
        <div className="space-y-2 text-[15px]">
          <p>{profileLine(member)}</p>
          {member.weight_kg != null && <p className="text-sm text-ink-soft">体重 {member.weight_kg} kg（只有你能看到）</p>}
          {gaps.length > 0 && <p className="text-sm text-amber-800">还缺：{gaps.join("、")}</p>}
          {result && <SavedNote result={result} />}
        </div>
      )}
    </Section>
  );
}

function SavedNote({ result }: { result: ProfileResult }) {
  if (!result.changed) return <p className="text-sm text-ink-soft">资料没有变化。</p>;
  const more = result.changelog.slice(1); // 第一行是"某某更新了自己的资料"
  return (
    <div role="status" className="space-y-1 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
      <div className="font-medium">已保存，行程更新到第 {result.version} 版</div>
      {more.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5">
          {more.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
      {result.issues.length > 0 && (
        <div className="pt-1 text-amber-900">
          行程检查有 {result.issues.length} 条新提醒：
          <ul className="list-disc space-y-0.5 pl-5">
            {result.issues.map((i) => (
              <li key={i.code + i.message}>{i.message}</li>
            ))}
          </ul>
          可以在对话里请猪猪帮忙调整行程。
        </div>
      )}
    </div>
  );
}
