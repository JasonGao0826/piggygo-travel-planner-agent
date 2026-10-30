"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, type JoinCheck } from "@/lib/api";
import { loadSession } from "@/lib/session";
import { Button, Spinner } from "./ui";

/**
 * 打开专属邀请链接：登录成对应成员，然后进入行程。
 * 如果这台设备在这个行程里已经是另一个人，先问一句，免得被别人发来的链接悄悄换了身份。
 */
export function JoinView({ token }: { token: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [ask, setAsk] = useState<JoinCheck | null>(null);
  const [joining, setJoining] = useState(false);
  const started = useRef(false);

  const enter = useCallback(
    (tripId: string) => router.replace(`/trips/${encodeURIComponent(tripId)}`), // replace：历史里不留带 token 的地址
    [router],
  );

  const join = useCallback(async () => {
    setJoining(true);
    try {
      const { trip_id } = await api.join(token);
      await loadSession(true);
      enter(trip_id);
    } catch (e) {
      setError(errorText(e));
      setJoining(false);
    }
  }, [token, enter]);

  useEffect(() => {
    if (started.current) return; // 开发模式下 effect 会跑两次
    started.current = true;
    (async () => {
      try {
        const check = await api.joinCheck(token);
        if (check.current_member_id && check.current_member_id !== check.member_id) setAsk(check);
        else await join();
      } catch (e) {
        setError(errorText(e));
      }
    })();
  }, [token, join]);

  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-4 py-16 text-center">
      {/* eslint-disable-next-line @next/next/no-img-element -- 静态小图，不需要图片优化 */}
      <img src="/brand/piggy.png" alt="" width={96} height={110} className="h-24 w-auto" />
      <p className="mt-3 text-xl font-bold text-cocoa">小猪游记 PiggyGo</p>
      {error ? (
        <div className="mt-4 rounded-xl border border-line bg-white px-5 py-6">
          <div className="text-[17px] font-semibold">打不开这个链接</div>
          <p className="mt-1 text-ink-soft">{error}</p>
          <Link href="/" className="mt-4 inline-block text-accent underline underline-offset-2">
            回到我的行程
          </Link>
        </div>
      ) : ask ? (
        <div className="mt-4 rounded-xl border border-line bg-white px-5 py-6">
          <div className="text-[17px] font-semibold">要切换身份吗？</div>
          <p className="mt-1 text-ink-soft">
            在「{ask.title}」里，这台设备现在是 <b className="text-ink">{ask.current_name}</b>。这个链接是{" "}
            <b className="text-ink">{ask.name}</b> 的专属链接，只有你就是 {ask.name} 本人时才切换。
          </p>
          <div className="mt-4 flex flex-col gap-2">
            <Button variant="primary" onClick={() => enter(ask.trip_id)} disabled={joining}>
              继续用 {ask.current_name} 的身份
            </Button>
            <Button onClick={join} busy={joining} disabled={joining}>
              切换成 {ask.name}
            </Button>
          </div>
        </div>
      ) : (
        <div className="mt-4 flex items-center gap-2 text-ink-soft">
          <Spinner /> 正在进入行程…
        </div>
      )}
    </div>
  );
}
