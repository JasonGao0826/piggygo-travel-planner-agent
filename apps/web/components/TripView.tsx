"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
  LIVE_CODES,
  api,
  errorText,
  type ActivityEvent,
  type AgentResponse,
  type GroupView,
  type Issue,
  type LiveReport,
  type MoneySummary,
  type Report,
  type SessionInfo,
  type TripDetail,
  type VersionEntry,
  type Voucher,
} from "@/lib/api";
import { loadSession, sessionMember } from "@/lib/session";
import { memberKey, readStorage, writeStorage } from "@/lib/storage";
import { CONTENT_TABS, sectionsByTab, type ContentTab } from "@/lib/tabs";
import { ChatPanel, type TimelineItem } from "./ChatPanel";
import { ConfirmNeedsBar, type ConfirmNeed } from "./ConfirmNeeds";
import { ActivityFeed } from "./ActivityFeed";
import { EvidenceDialog } from "./EvidenceDialog";
import { GroupPanel } from "./GroupPanel";
import { InvitePanel } from "./InvitePanel";
import { IssuesBanner } from "./issues";
import { LivePanel } from "./LivePanel";
import { MemberSwitcher } from "./MemberSwitcher";
import { MoneyPanel } from "./MoneyPanel";
import { MyProfile } from "./MyProfile";
import type { PatchBusy } from "./PatchCard";
import { profileGaps } from "./ProfileFields";
import { CoverCard, DailyView, ReportSectionView } from "./report";
import { Badge, ErrorBox, Spinner, cx } from "./ui";
import { VersionsPanel } from "./VersionsPanel";
import { VoucherPanel, type ItemOption } from "./VoucherPanel";

const ACTION_CLASS =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-lg border border-line bg-white px-2.5 text-sm font-medium whitespace-nowrap text-ink hover:bg-canvas sm:px-3 sm:text-[15px]";

function agentItems(res: AgentResponse): TimelineItem[] {
  const items: TimelineItem[] = [{ kind: "message", role: "assistant", text: res.reply?.trim() || "（猪猪没有回复内容）" }];
  const failed = (res.tool_calls ?? []).filter((c) => c.error);
  if (failed.length) {
    items.push({
      kind: "notice",
      tone: "info",
      text: "有几次查询没成功，猪猪的回答可能不完整",
      lines: failed.map((c) => `${c.name}：${c.error}`),
    });
  }
  return items;
}

export function TripView({ tripId }: { tripId: string }) {
  const [detail, setDetail] = useState<TripDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; notFound: boolean; needsInvite: boolean } | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [sessionFailed, setSessionFailed] = useState(false); // 读登录状态失败：不知道是不是上线模式，先锁住身份
  const [report, setReport] = useState<Report | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [versions, setVersions] = useState<VersionEntry[] | null>(null);
  const [versionsError, setVersionsError] = useState<string | null>(null);
  const [live, setLive] = useState<LiveReport | null>(null);
  const [liveLoading, setLiveLoading] = useState(false);
  const [liveError, setLiveError] = useState<string | null>(null);
  const [group, setGroup] = useState<GroupView | null>(null);
  const [groupError, setGroupError] = useState<string | null>(null);
  const [activity, setActivity] = useState<ActivityEvent[] | null>(null);
  const [activityError, setActivityError] = useState<string | null>(null);
  const [vouchers, setVouchers] = useState<Voucher[] | null>(null);
  const [vouchersError, setVouchersError] = useState<string | null>(null);
  const [money, setMoney] = useState<MoneySummary | null>(null);
  const [moneyError, setMoneyError] = useState<string | null>(null);
  const [confirmNeeds, setConfirmNeeds] = useState<ConfirmNeed[]>([]);
  const [creatingTodos, setCreatingTodos] = useState(false);

  const [memberId, setMemberId] = useState<string | null>(null);
  const [tab, setTab] = useState<ContentTab>("overview");
  const [chatOpen, setChatOpen] = useState(false); // 手机上"对话"页签

  const [items, setItems] = useState<TimelineItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [sending, setSending] = useState<{ since: number; label: string } | null>(null);
  const [patchBusy, setPatchBusy] = useState<Record<string, PatchBusy>>({});
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const historyFor = useRef<string | null>(null);

  // ---------- 读取 ----------

  const loadTrip = useCallback(async () => {
    try {
      const d = await api.getTrip(tripId);
      setDetail(d);
      setLoadError(null);
      return d;
    } catch (e) {
      setLoadError({
        message: errorText(e),
        notFound: e instanceof ApiError && e.status === 404,
        needsInvite: e instanceof ApiError && (e.status === 401 || e.status === 403),
      });
      return null;
    }
  }, [tripId]);

  const loadReport = useCallback(async () => {
    try {
      setReport(await api.getReport(tripId));
      setReportError(null);
    } catch (e) {
      setReportError(errorText(e));
    }
  }, [tripId]);

  const loadVersions = useCallback(async () => {
    try {
      setVersions(await api.getVersions(tripId));
      setVersionsError(null);
    } catch (e) {
      setVersionsError(errorText(e));
    }
  }, [tripId]);

  const loadLive = useCallback(async () => {
    setLiveLoading(true);
    try {
      setLive(await api.getLive(tripId));
      setLiveError(null);
    } catch (e) {
      setLiveError(errorText(e));
    } finally {
      setLiveLoading(false);
    }
  }, [tripId]);

  const loadGroup = useCallback(async () => {
    try {
      setGroup(await api.getGroup(tripId));
      setGroupError(null);
    } catch (e) {
      setGroupError(errorText(e));
    }
  }, [tripId]);

  const loadActivity = useCallback(async () => {
    try {
      setActivity(await api.getActivity(tripId));
      setActivityError(null);
    } catch (e) {
      setActivityError(errorText(e));
    }
  }, [tripId]);

  const loadVouchers = useCallback(async () => {
    try {
      setVouchers(await api.listVouchers(tripId));
      setVouchersError(null);
    } catch (e) {
      setVouchersError(errorText(e));
    }
  }, [tripId]);

  const loadConfirmNeeds = useCallback(async () => {
    try {
      setConfirmNeeds(await api.getConfirmNeeds(tripId));
    } catch {
      setConfirmNeeds([]); // 只是提示，读不到就不显示
    }
  }, [tripId]);

  const loadMoney = useCallback(async () => {
    try {
      setMoney(await api.getMoney(tripId));
      setMoneyError(null);
    } catch (e) {
      setMoneyError(errorText(e));
    }
  }, [tripId]);

  const loadHistory = useCallback(
    async (mid: string) => {
      historyFor.current = mid;
      setHistoryLoading(true);
      setHistoryError(null);
      try {
        const msgs = await api.getMessages(tripId, mid);
        if (historyFor.current !== mid) return; // 已经切换到别的成员
        setItems(msgs.map((m) => ({ kind: "message", role: m.role, text: m.text })));
      } catch (e) {
        if (historyFor.current !== mid) return;
        setItems([]);
        setHistoryError(errorText(e));
      } finally {
        if (historyFor.current === mid) setHistoryLoading(false);
      }
    },
    [tripId],
  );

  useEffect(() => {
    (async () => {
      const [d, first] = await Promise.all([loadTrip(), loadSession(true).catch(() => null)]);
      let s = first;
      for (let i = 0; s === null && i < 3; i++) {
        await new Promise((r) => setTimeout(r, 1500 * (i + 1))); // 读登录状态失败：等一下再试
        s = await loadSession(true).catch(() => null);
      }
      setSession(s);
      setSessionFailed(s === null);
      if (!d || s === null) return; // 不知道是谁：不猜，页面上提示刷新
      // 用邀请链接登录的：身份固定是链接对应的成员
      const mine = sessionMember(s, tripId);
      if (mine) {
        setMemberId(mine);
        return;
      }
      const ids = d.trip.members.map((m) => m.id);
      const saved = readStorage(memberKey(tripId));
      const owner = d.trip.members.find((m) => m.role === "owner")?.id;
      setMemberId(saved && ids.includes(saved) ? saved : (owner ?? ids[0] ?? null));
    })();
    loadReport();
    loadVersions();
    loadLive();
    loadGroup();
    loadActivity();
    loadVouchers();
  }, [tripId, loadTrip, loadReport, loadVersions, loadLive, loadGroup, loadActivity, loadVouchers]);

  // 别人上传的凭证、记的账不会推送过来：打开"凭证""账本"页签时重新读一次
  useEffect(() => {
    if (tab === "vouchers") loadVouchers();
    if (tab === "money") loadMoney();
    if (tab === "todo") loadConfirmNeeds();
  }, [tab, loadVouchers, loadMoney, loadConfirmNeeds]);

  // 别人投票、改偏好不会推送过来：打开"大家""动态"页签时，以及每分钟，重新读一次
  useEffect(() => {
    if (tab !== "group" && tab !== "activity") return;
    loadGroup();
    loadActivity();
    const timer = window.setInterval(() => {
      loadGroup();
      loadActivity();
    }, 60 * 1000);
    return () => window.clearInterval(timer);
  }, [tab, loadGroup, loadActivity]);

  // 页面开着的时候每 15 分钟更新一次实时情况（后端也在定时刷新）
  useEffect(() => {
    const timer = window.setInterval(loadLive, 15 * 60 * 1000);
    return () => window.clearInterval(timer);
  }, [loadLive]);

  useEffect(() => {
    if (memberId) loadHistory(memberId);
  }, [memberId, loadHistory]);

  useEffect(() => {
    if (detail) document.title = `${detail.trip.meta.title} · 小猪游记`;
  }, [detail]);

  const refreshAll = useCallback(async () => {
    await Promise.all([loadTrip(), loadReport(), loadVersions(), loadLive(), loadGroup(), loadActivity(), loadVouchers(), loadConfirmNeeds()]);
  }, [loadTrip, loadReport, loadVersions, loadLive, loadGroup, loadActivity, loadVouchers, loadConfirmNeeds]);

  // ---------- 操作 ----------

  function switchMember(id: string) {
    setMemberId(id);
    writeStorage(memberKey(tripId), id);
  }

  function selectTab(t: ContentTab | "chat") {
    if (t === "chat") setChatOpen(true);
    else {
      setTab(t);
      setChatOpen(false);
    }
    if (window.scrollY > 120) window.scrollTo({ top: 0 });
  }

  const append = (...more: TimelineItem[]) => setItems((prev) => [...prev, ...more]);

  async function send(text: string): Promise<boolean> {
    if (!memberId) return false;
    const mine: TimelineItem = { kind: "message", role: "user", text };
    append(mine);
    setSending({ since: Date.now(), label: "猪猪正在查资料…" });
    try {
      const res = await api.chat(tripId, memberId, text);
      append(...agentItems(res));
      await Promise.all([loadTrip(), loadGroup(), loadActivity(), loadMoney()]); // 新提案、新决定、记下的偏好、投票和账目草稿
      return true;
    } catch (e) {
      setItems((prev) => [
        ...prev.filter((x) => x !== mine),
        { kind: "notice", tone: "error", text: "消息没有发出去，原话已经放回输入框", lines: [errorText(e)] },
      ]);
      return false;
    } finally {
      setSending(null);
    }
  }

  const setBusy = (id: string, v: PatchBusy) =>
    setPatchBusy((b) => {
      const next = { ...b };
      if (v) next[id] = v;
      else delete next[id];
      return next;
    });

  async function confirm(patchId: string) {
    if (!memberId) return;
    const patch = detail?.pending.find((p) => p.id === patchId);
    setBusy(patchId, "confirm");
    try {
      const res = await api.confirmPatch(tripId, patchId, memberId);
      append({
        kind: "notice",
        tone: "success",
        text: `已确认${patch ? `“${patch.summary}”` : "改动"}，生成第 ${res.version} 版`,
        lines: res.changelog,
      });
      await refreshAll();
    } catch (e) {
      const conflict = e instanceof ApiError && e.status === 409;
      append({
        kind: "notice",
        tone: "error",
        text: conflict ? "这个改动和最新版本冲突，没有写入。可以撤回后让猪猪重新提出。" : "确认没有成功",
        lines: [errorText(e)],
      });
      await loadTrip();
    } finally {
      setBusy(patchId, null);
    }
  }

  async function reject(patchId: string) {
    const patch = detail?.pending.find((p) => p.id === patchId);
    setBusy(patchId, "reject");
    try {
      await api.rejectPatch(tripId, patchId);
      append({ kind: "notice", tone: "info", text: `已撤回${patch ? `“${patch.summary}”` : "改动"}，行程没有变化` });
      await loadTrip();
    } catch (e) {
      append({ kind: "notice", tone: "error", text: "撤回没有成功", lines: [errorText(e)] });
    } finally {
      setBusy(patchId, null);
    }
  }

  async function createConfirmTodos() {
    if (!memberId) return;
    setCreatingTodos(true);
    try {
      const patch = await api.createConfirmTodos(tripId, memberId);
      append({ kind: "notice", tone: "info", text: `已提出改动「${patch.summary}」，确认后写进待办`, lines: patch.changes.slice(0, 6) });
      await loadTrip();
      setChatOpen(true);
    } catch (e) {
      append({ kind: "notice", tone: "error", text: "没有生成待确认待办", lines: [errorText(e)] });
      setChatOpen(true);
    } finally {
      setCreatingTodos(false);
    }
  }

  function askAssistant(text: string) {
    setChatOpen(true);
    send(text);
  }

  function onGroupUpdated(g: GroupView, decisionsChanged?: boolean) {
    setGroup(g);
    loadActivity();
    if (decisionsChanged) loadTrip();
  }

  function askAboutLive(issue: Issue) {
    setChatOpen(true);
    send(`【实时提醒】${issue.message}\n请看看要不要调整受影响的安排；需要改的话直接提出改动。`);
  }

  function onEvidenceApplied(res: AgentResponse, filename: string) {
    setEvidenceFile(null);
    append({ kind: "notice", tone: "info", text: `上传了凭证 ${filename}，核对后的内容已经交给猪猪和行程比对` }, ...agentItems(res));
    setChatOpen(true);
    loadTrip();
    loadVouchers();
    loadActivity();
    loadMoney(); // 凭证上有金额时会顺带生成账目草稿
  }

  const byTab = useMemo(() => (report ? sectionsByTab(report) : null), [report]);

  // ---------- 渲染 ----------

  if (loadError && !detail) {
    return (
      <div className="mx-auto max-w-xl px-4 py-16">
        {loadError.needsInvite ? (
          <div className="rounded-xl border border-line bg-white px-5 py-8 text-center">
            <div className="text-[17px] font-semibold">需要专属邀请链接</div>
            <p className="mt-1 text-ink-soft">请打开发起人发给你的链接。链接只能自己用，换手机也用同一个链接打开。</p>
            <Link href="/" className="mt-4 inline-block text-accent underline underline-offset-2">
              回到我的行程
            </Link>
          </div>
        ) : loadError.notFound ? (
          <div className="rounded-xl border border-line bg-white px-5 py-8 text-center">
            <div className="text-[17px] font-semibold">找不到这个行程</div>
            <p className="mt-1 text-ink-soft">可能链接有误，或者后端重启后数据没有保留。</p>
            <Link href="/" className="mt-4 inline-block text-accent underline underline-offset-2">
              回到我的行程
            </Link>
          </div>
        ) : (
          <ErrorBox onRetry={loadTrip}>{loadError.message}</ErrorBox>
        )}
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex items-center justify-center gap-2 py-24 text-ink-soft">
        <Spinner /> 正在读取行程…
      </div>
    );
  }

  const { trip, pending } = detail;
  // 凭证可以关联的安排：需要预订的活动、航班、租车等（按时间排）
  type DayLike = { date: string; items: { id: string; title: string; type: string }[] };
  const itemOptions: ItemOption[] = ((trip.days as DayLike[] | undefined) ?? []).flatMap((d) =>
    d.items
      .filter((i) => ["activity", "flight", "car_pickup", "checkin", "transfer", "meal"].includes(i.type))
      .map((i) => ({ id: i.id, label: i.title, date: d.date })),
  );
  const issues = detail.issues.filter((i) => !LIVE_CODES.has(i.code)); // 实时类提醒放在"实时情况"里
  const member = trip.members.find((m) => m.id === memberId);
  const locked = session?.mode === "invite" || sessionFailed; // 邀请链接模式（或还没确认）：不能切换身份

  const actions = (
    <>
      <MemberSwitcher members={trip.members} value={memberId ?? ""} onChange={switchMember} disabled={!!sending} locked={locked} />
      <div className="ml-auto flex gap-2">
        <button type="button" className={ACTION_CLASS} onClick={() => fileRef.current?.click()} disabled={!memberId}>
          上传凭证
        </button>
        <details className="relative">
          <summary className={cx(ACTION_CLASS, "cursor-pointer list-none")}>下载 ▾</summary>
          <div className="absolute right-0 z-40 mt-1 w-60 rounded-lg border border-line bg-white p-1 shadow-lg">
            {[
              { href: api.bundleUrl(tripId), label: "行程包（ZIP）", hint: "Word + 日历 + 全部凭证" },
              { href: api.reportDocxUrl(tripId), label: "Word 行程报告", hint: "可以直接改的表格" },
              { href: api.calendarUrl(tripId), label: "日历文件（.ics）", hint: "导入手机日历，每个安排都有提醒" },
            ].map((d) => (
              <a key={d.label} href={d.href} download className="block rounded-md px-3 py-2 hover:bg-canvas">
                <span className="block text-[15px] font-medium">{d.label}</span>
                <span className="block text-xs text-ink-faint">{d.hint}</span>
              </a>
            ))}
          </div>
        </details>
      </div>
    </>
  );

  const pendingBadge = pending.length > 0 && (
    <span className="ml-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1 text-xs text-white">
      {pending.length}
    </span>
  );

  // "大家"页签上的数字：我还没投的投票，加上我还没填偏好、我的资料还缺东西
  const groupTodo =
    (group
      ? group.polls.filter((p) => p.status === "open" && memberId && !(memberId in p.votes)).length +
        (group.members.some((m) => m.id === memberId && !m.filled) ? 1 : 0)
      : 0) + (member && profileGaps(member).length > 0 ? 1 : 0);
  const groupBadge = groupTodo > 0 && (
    <span className="ml-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-amber-500 px-1 text-xs text-white">
      {groupTodo}
    </span>
  );

  const tabButton = (key: ContentTab | "chat", label: string, extra?: React.ReactNode) => {
    const active = key === "chat" ? chatOpen : tab === key && !chatOpen;
    // 在桌面上对话一直显示在右栏，内容页签按 tab 高亮
    const desktopActive = key !== "chat" && tab === key;
    return (
      <button
        key={key}
        type="button"
        onClick={() => selectTab(key)}
        aria-current={active ? "page" : undefined}
        className={cx(
          "relative inline-flex h-11 shrink-0 items-center gap-1 px-2.5 text-[15px] whitespace-nowrap sm:px-3",
          "after:absolute after:inset-x-2.5 after:bottom-0 after:h-0.5 after:rounded-full sm:after:inset-x-3",
          active ? "max-lg:font-semibold max-lg:text-accent max-lg:after:bg-accent" : "max-lg:text-ink-soft",
          desktopActive ? "lg:font-semibold lg:text-accent lg:after:bg-accent" : "lg:text-ink-soft",
          key === "chat" && "lg:hidden",
          "hover:text-ink",
        )}
      >
        {label}
        {extra}
      </button>
    );
  };

  const empty = <p className="rounded-xl border border-dashed border-line bg-white px-4 py-6 text-center text-ink-soft">这部分暂时没有内容。</p>;

  function content() {
    if (tab === "group") {
      return memberId ? (
        <div className="space-y-4">
          {session?.mode === "invite" && member?.role === "owner" && <InvitePanel tripId={tripId} memberId={memberId} />}
          {member && <MyProfile key={member.id} tripId={tripId} member={member} onSaved={refreshAll} />}
          <GroupPanel
            tripId={tripId}
            memberId={memberId}
            group={group}
            error={groupError}
            onRetry={loadGroup}
            onUpdated={onGroupUpdated}
            onAsk={askAssistant}
          />
        </div>
      ) : null;
    }
    if (tab === "vouchers") {
      return memberId ? (
        <VoucherPanel
          tripId={tripId}
          memberId={memberId}
          memberRole={member?.role ?? "member"}
          vouchers={vouchers}
          error={vouchersError}
          items={itemOptions}
          onRetry={loadVouchers}
          onChanged={() => {
            loadVouchers();
            loadActivity();
            loadReport();
          }}
          onRecognize={() => fileRef.current?.click()}
        />
      ) : null;
    }
    if (tab === "money") {
      return memberId ? (
        <MoneyPanel
          tripId={tripId}
          memberId={memberId}
          members={trip.members}
          items={itemOptions}
          money={money}
          error={moneyError}
          onRetry={loadMoney}
          onUpdated={(m) => {
            setMoney(m);
            loadActivity();
            loadReport();
          }}
        />
      ) : null;
    }
    if (tab === "activity") {
      return <ActivityFeed events={activity} members={trip.members} error={activityError} onRetry={loadActivity} />;
    }
    if (tab === "versions") {
      return <VersionsPanel versions={versions} current={detail!.version} decisions={detail!.decisions} error={versionsError} />;
    }
    if (!report || !byTab) {
      return reportError ? (
        <ErrorBox onRetry={loadReport}>行程内容读取失败：{reportError}</ErrorBox>
      ) : (
        <div className="flex items-center gap-2 py-10 text-ink-soft">
          <Spinner /> 正在整理行程内容…
        </div>
      );
    }
    const sections = byTab[tab];
    if (tab === "overview") {
      return (
        <div className="space-y-6">
          <CoverCard report={report} />
          {sections.map((s, i) => (
            <ReportSectionView key={i} section={s} />
          ))}
        </div>
      );
    }
    if (!sections.length) return empty;
    if (tab === "daily") return <DailyView sections={sections} />;
    if (tab === "todo") {
      return (
        <div className="space-y-6">
          <ConfirmNeedsBar needs={confirmNeeds} busy={creatingTodos} onCreate={createConfirmTodos} />
          {sections.map((s, i) => (
            <ReportSectionView key={i} section={s} />
          ))}
        </div>
      );
    }
    return (
      <div className="space-y-6">
        {sections.map((s, i) => (
          <ReportSectionView key={i} section={s} />
        ))}
      </div>
    );
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-30 border-b border-line bg-white">
        <div className="mx-auto max-w-7xl px-4">
          <div className="flex h-14 items-center gap-2">
            <Link
              href="/"
              aria-label="返回我的行程"
              className="-ml-2 inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-ink-soft hover:bg-canvas hover:text-ink"
            >
              <svg className="h-5 w-5" viewBox="0 0 20 20" aria-hidden>
                <path d="M12.5 4.5 7 10l5.5 5.5" stroke="currentColor" strokeWidth="1.8" fill="none" strokeLinecap="round" />
              </svg>
            </Link>
            {/* eslint-disable-next-line @next/next/no-img-element -- 静态小图 */}
            <img src="/brand/piggy.png" alt="小猪游记" width={28} height={32} className="hidden h-8 w-auto shrink-0 sm:block" />
            <h1 className="min-w-0 truncate text-[17px] font-semibold">{trip.meta.title}</h1>
            <Badge tone="accent">V{detail.version}</Badge>
            <div className="ml-auto hidden items-center gap-2 lg:flex">{actions}</div>
          </div>
          <nav className="no-scrollbar -mx-4 flex overflow-x-auto px-2 lg:px-1" aria-label="行程页签">
            {/* 手机上"对话"放在"版本"前面，窄屏也能直接看到 */}
            {CONTENT_TABS.filter((t) => t.key !== "versions").map((t) => tabButton(t.key, t.label, t.key === "group" ? groupBadge : undefined))}
            {tabButton("chat", "对话", pendingBadge)}
            {tabButton("versions", "版本")}
          </nav>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-4 pt-3 pb-6 lg:pt-5">
        <div className="mb-3 flex flex-wrap items-center gap-2 lg:hidden">{actions}</div>

        <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_360px] lg:gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
          <main className={cx("min-w-0 space-y-4", chatOpen && "max-lg:hidden")}>
            {sessionFailed && (
              <ErrorBox onRetry={() => window.location.reload()}>没能确认你的身份（网络不稳定？），请刷新页面再试。</ErrorBox>
            )}
            <IssuesBanner key={issues.map((i) => i.code + i.message).join("|")} issues={issues} />
            <LivePanel
              live={live}
              loading={liveLoading}
              error={liveError}
              onRetry={loadLive}
              onAsk={askAboutLive}
              disabled={!!sending || !memberId}
            />
            {pending.length > 0 && (
              <button
                type="button"
                onClick={() => selectTab("chat")}
                className="flex min-h-11 w-full items-center justify-between rounded-xl border border-accent-line bg-accent-soft px-4 py-2 text-left text-[15px] text-accent lg:hidden"
              >
                <span className="font-medium">有 {pending.length} 个改动等待确认</span>
                <span>去确认 →</span>
              </button>
            )}
            {content()}
          </main>

          <aside
            className={cx(
              "min-w-0 lg:sticky lg:top-[117px] lg:h-[calc(100dvh-133px)]",
              !chatOpen && "max-lg:hidden",
            )}
            aria-label="和猪猪对话"
          >
            <ChatPanel
              memberName={member?.name ?? "成员"}
              items={items}
              loadingHistory={historyLoading}
              historyError={historyError}
              onRetryHistory={() => memberId && loadHistory(memberId)}
              sending={sending}
              onSend={send}
              pending={pending}
              patchBusy={patchBusy}
              onConfirm={confirm}
              onReject={reject}
              active={chatOpen}
            />
          </aside>
        </div>
      </div>

      {!chatOpen && (
        <button
          type="button"
          onClick={() => selectTab("chat")}
          className="fixed right-4 bottom-[max(1rem,env(safe-area-inset-bottom))] z-20 inline-flex h-12 items-center gap-2 rounded-full bg-accent pr-4 pl-3.5 text-[15px] font-medium text-white shadow-lg shadow-black/15 hover:bg-accent-hover lg:hidden"
        >
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" aria-hidden>
            <path
              d="M5 4h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-8l-5 4v-4H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinejoin="round"
            />
          </svg>
          问猪猪
          {pending.length > 0 && (
            <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-white px-1 text-xs text-accent">
              {pending.length}
            </span>
          )}
        </button>
      )}

      <input
        ref={fileRef}
        type="file"
        accept="image/*,application/pdf,.pdf,.eml,message/rfc822"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = ""; // 同一个文件可以再选一次
          if (f) setEvidenceFile(f);
        }}
      />
      {evidenceFile && memberId && (
        <EvidenceDialog
          tripId={tripId}
          memberId={memberId}
          file={evidenceFile}
          onClose={() => setEvidenceFile(null)}
          onApplied={onEvidenceApplied}
        />
      )}
    </div>
  );
}
