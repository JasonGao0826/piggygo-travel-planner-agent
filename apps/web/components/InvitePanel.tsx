"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, joinUrl, type InviteStatus } from "@/lib/api";
import { ROLE_LABEL, shortDate } from "@/lib/format";
import { Avatar, Button, Card, ErrorBox, Spinner, inputClass } from "./ui";

/** 一条邀请链接：只读输入框 + 复制按钮（复制不了时选中文字，让用户自己复制）。 */
export function CopyLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const ref = useRef<HTMLInputElement>(null);

  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      ref.current?.select();
    }
  }

  return (
    <div className="flex gap-2">
      <input ref={ref} readOnly value={url} className={inputClass} onFocus={(e) => e.target.select()} aria-label="邀请链接" />
      <Button onClick={copy} className="shrink-0">
        {copied ? "已复制" : "复制"}
      </Button>
    </div>
  );
}

/** 发起人管理大家的专属邀请链接（只在上线的邀请链接模式下显示）。 */
export function InvitePanel({ tripId, memberId }: { tripId: string; memberId: string }) {
  const [rows, setRows] = useState<InviteStatus[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [fresh, setFresh] = useState<Record<string, string>>({}); // 刚生成的链接，只在这次显示

  const load = useCallback(async () => {
    try {
      setRows(await api.listInvites(tripId, memberId));
      setError(null);
    } catch (e) {
      setError(errorText(e));
    }
  }, [tripId, memberId]);

  useEffect(() => {
    load();
  }, [load]);

  async function generate(row: InviteStatus) {
    const who = row.member_id === memberId ? "你自己" : row.name;
    if (row.has_link && !window.confirm(`重新生成${who}的链接？旧链接会立即失效，用旧链接登录的设备要用新链接重新打开。`)) return;
    setBusy(row.member_id);
    try {
      const { token } = await api.newInvite(tripId, row.member_id, memberId);
      setFresh((f) => ({ ...f, [row.member_id]: joinUrl(token) }));
      await load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="p-4 sm:p-5">
      <h2 className="text-[17px] font-semibold">邀请大家加入</h2>
      <p className="mt-1 text-sm text-ink-soft">
        每人一个专属链接，点开就是本人身份，不用注册。链接等于钥匙：只私发给本人，不要发到群里。
        为了安全，链接只在生成时显示一次；忘了就重新生成（旧的会失效）。
      </p>
      {error && (
        <div className="mt-3">
          <ErrorBox onRetry={load}>{error}</ErrorBox>
        </div>
      )}
      {rows === null && !error && (
        <div className="mt-3 flex items-center gap-2 text-ink-soft">
          <Spinner /> 正在读取…
        </div>
      )}
      {rows && (
        <ul className="mt-3 divide-y divide-line">
          {rows.map((row, i) => (
            <li key={row.member_id} className="py-3">
              <div className="flex items-center gap-2">
                <Avatar name={row.name} size={28} index={i} />
                <div className="min-w-0 flex-1">
                  <div className="font-medium">
                    {row.name}
                    {row.member_id === memberId && <span className="ml-1 text-sm text-ink-faint">（我）</span>}
                    {ROLE_LABEL[row.role] && <span className="ml-1 text-sm text-ink-faint">{ROLE_LABEL[row.role]}</span>}
                  </div>
                  <div className="text-xs text-ink-faint">
                    {row.has_link && row.created_at ? `链接生成于 ${shortDate(row.created_at.slice(0, 10))}` : "还没有链接"}
                  </div>
                </div>
                <Button onClick={() => generate(row)} busy={busy === row.member_id} disabled={!!busy} className="shrink-0">
                  {row.has_link ? "重新生成" : "生成链接"}
                </Button>
              </div>
              {fresh[row.member_id] && (
                <div className="mt-2">
                  <CopyLink url={fresh[row.member_id]} />
                  <p className="mt-1 text-xs text-ink-faint">
                    {row.member_id === memberId ? "用这个链接在你的其他设备上打开。" : `复制后私发给${row.name}。`}
                  </p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
