"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, errorText, type TripSummary } from "@/lib/api";
import { DESTINATION_LABEL, dateRange } from "@/lib/format";
import { loadSession, useSession } from "@/lib/session";
import { AvatarStack, Badge, Button, ButtonLink, ErrorBox, Spinner } from "@/components/ui";

// 每步一行：手机（360px 宽）上也不折行
const HOW_TO = [
  { title: "建行程", text: "填份问卷，猪猪排出第一版" },
  { title: "发链接", text: "给每个朋友私发专属链接" },
  { title: "一起改", text: "跟猪猪聊着改，分歧就投票" },
  { title: "出发", text: "清单打勾，路上 AA 记账" },
];

function HowTo() {
  return (
    <section aria-labelledby="how-to" className="mb-8 rounded-xl border border-line bg-white px-4 py-4 sm:px-5">
      <h2 id="how-to" className="text-[17px] font-semibold">
        怎么用
      </h2>
      <ol className="mt-3 grid gap-2.5 sm:grid-cols-2 sm:gap-x-6">
        {HOW_TO.map((s, i) => (
          <li key={s.title} className="flex items-start gap-2.5">
            <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-sm font-semibold text-white">
              {i + 1}
            </span>
            <p className="min-w-0 text-[15px] leading-6">
              <span className="font-semibold">{s.title}</span>
              <span className="ml-2 text-ink-soft">{s.text}</span>
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export default function HomePage() {
  const router = useRouter();
  const [trips, setTrips] = useState<TripSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const session = useSession();
  const invite = session?.mode === "invite"; // 上线后：只看得到自己加入的行程，演示行程由管理员载入

  const load = useCallback(async () => {
    setError(null);
    try {
      setTrips(await api.listTrips());
    } catch (e) {
      setError(errorText(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function logout() {
    if (!window.confirm("在这台设备上退出？之后要用你的专属链接重新打开。")) return;
    try {
      await api.logout();
      await loadSession(true);
      window.location.reload();
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function loadDemo() {
    setLoadingDemo(true);
    setError(null);
    try {
      const { id } = await api.createDemo();
      router.push(`/trips/${encodeURIComponent(id)}`);
    } catch (e) {
      setError(errorText(e));
      setLoadingDemo(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 pt-8 pb-16 sm:pt-12">
      <header className="mb-8 flex items-center gap-4 rounded-2xl bg-piggy-soft px-4 py-4 sm:gap-6 sm:px-6 sm:py-5">
        {/* eslint-disable-next-line @next/next/no-img-element -- 静态小图，不需要图片优化 */}
        <img src="/brand/piggy.png" alt="" width={96} height={110} className="h-20 w-auto shrink-0 sm:h-24" />
        <div className="min-w-0">
          <p className="text-[22px] leading-tight font-bold text-cocoa sm:text-2xl">
            小猪游记 <span className="text-base font-semibold text-accent">PiggyGo</span>
          </p>
          <p className="mt-1 text-[15px] font-medium text-cocoa/80">交给猪猪，你负责开心出发！</p>
          <p className="mt-1 text-sm text-ink-soft">和同行的朋友一起排行程，改动由猪猪提出、大家确认后生效。</p>
        </div>
      </header>

      <HowTo />

      <h1 className="mb-3 text-xl font-semibold">我的行程</h1>

      <div className="mb-6 flex flex-col gap-2 sm:flex-row">
        <ButtonLink href="/new" variant="primary" className="sm:px-5">
          ＋ 新建行程
        </ButtonLink>
        {session && !invite && (
          <Button onClick={loadDemo} busy={loadingDemo} disabled={loadingDemo}>
            载入新西兰演示行程
          </Button>
        )}
      </div>

      {invite && trips && trips.length > 0 && (
        <p className="mb-4 text-sm text-ink-faint">
          借用别人的手机或电脑打开的？用完可以
          <button type="button" onClick={logout} className="ml-1 text-accent underline underline-offset-2">
            在这台设备上退出
          </button>
        </p>
      )}

      {error && (
        <div className="mb-4">
          <ErrorBox onRetry={load}>{error}</ErrorBox>
        </div>
      )}

      {trips === null && !error && (
        <div className="flex items-center gap-2 py-10 text-ink-soft">
          <Spinner /> 正在读取行程…
        </div>
      )}

      {trips && trips.length === 0 && (
        <div className="rounded-xl border border-dashed border-line bg-white px-5 py-10 text-center text-ink-soft">
          {invite ? (
            <>
              还没有加入行程。请打开发起人私发给你的专属邀请链接。
              {session?.needs_beta_code && <span className="mt-1 block text-sm">有内测邀请码的话，也可以自己新建一个行程。</span>}
            </>
          ) : (
            "还没有行程。可以先载入新西兰演示行程看看效果，或者新建一个。"
          )}
        </div>
      )}

      {trips && trips.length > 0 && (
        <ul className="space-y-3">
          {trips.map((t) => (
            <li key={t.id}>
              <Link
                href={`/trips/${encodeURIComponent(t.id)}`}
                className="block rounded-xl border border-line bg-white p-4 transition-colors hover:border-accent-line hover:bg-accent-soft/30 sm:p-5"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-[17px] leading-snug font-semibold">{t.title}</h2>
                    <p className="mt-1 text-sm text-ink-soft">
                      {t.destination && DESTINATION_LABEL[t.destination] ? `${DESTINATION_LABEL[t.destination]} · ` : ""}
                      {dateRange(t.start_date, t.end_date)}
                    </p>
                  </div>
                  <Badge tone="accent">V{t.version}</Badge>
                </div>
                <div className="mt-3 flex items-center gap-2">
                  <AvatarStack names={t.members.map((m) => m.name)} />
                  <span className="truncate text-sm text-ink-soft">{t.members.map((m) => m.name).join("、")}</span>
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
