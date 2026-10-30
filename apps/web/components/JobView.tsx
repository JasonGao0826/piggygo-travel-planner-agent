"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, errorText, type Job, type JobQuestion, type JobSkeleton } from "@/lib/api";
import { Button, ButtonLink, ComingSoon, ErrorBox, Spinner, cx, inputClass } from "./ui";

const POLL_MS = 3000;

// 首次生成的阶段（规格 5.1）
const STAGES: { code: string; label: string }[] = [
  { code: "S0", label: "信息校验" },
  { code: "S1", label: "出发前事项" },
  { code: "S2", label: "路线框架" },
  { code: "S3", label: "住宿与交通" },
  { code: "S4", label: "项目排期" },
  { code: "S5", label: "逐小时时间表" },
  { code: "S6", label: "汇总" },
  { code: "S7", label: "报告" },
];

function stageIndex(stage: string | null | undefined): number {
  if (!stage) return -1;
  const m = /S(\d)/i.exec(stage);
  if (m) return Number(m[1]);
  return STAGES.findIndex((s) => stage.includes(s.label));
}

function questionText(q: string | JobQuestion): string {
  if (typeof q === "string") return q;
  return q.text || q.question || JSON.stringify(q);
}

type LogLine = { stage: string; message: string };

function airportName(id: string): string {
  return id.replace(/^(nz|au)_/, "").replace(/_airport$/, "").toUpperCase();
}

/** 路线框架：每晚住哪、每天路线、航班。用户看过后确认或提意见。 */
function SkeletonCard({ skeleton }: { skeleton: JobSkeleton }) {
  return (
    <div className="rounded-xl border border-line bg-white p-4 sm:p-5">
      <div className="text-[15px] font-semibold">路线框架</div>
      <p className="mt-1.5 text-[15px] leading-relaxed text-ink-soft">{skeleton.summary}</p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[480px] border-collapse text-sm">
          <thead>
            <tr className="bg-accent text-left text-white">
              <th className="px-2 py-1.5 font-medium">日期</th>
              <th className="px-2 py-1.5 font-medium">路线</th>
              <th className="px-2 py-1.5 font-medium">重点</th>
              <th className="px-2 py-1.5 font-medium">住</th>
            </tr>
          </thead>
          <tbody>
            {skeleton.days.map((d) => (
              <tr key={d.date} className="border-b border-line align-top">
                <td className="whitespace-nowrap px-2 py-1.5">{d.date.slice(5).replace("-", "/")}</td>
                <td className="px-2 py-1.5">{d.route}</td>
                <td className="px-2 py-1.5 text-ink-soft">{d.focus}</td>
                <td className="whitespace-nowrap px-2 py-1.5">{d.night_city || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {skeleton.flights.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {skeleton.flights.map((f, i) => (
            <li key={i}>
              ✈ {f.date.slice(5).replace("-", "/")} {f.number || "航班待订"} {airportName(f.from_airport)} →{" "}
              {airportName(f.to_airport)} {f.depart}–{f.arrive}
              <span className={cx("ml-1.5", f.booked ? "text-accent" : "text-amber-700")}>{f.booked ? "已订" : "建议"}</span>
            </li>
          ))}
        </ul>
      )}
      {skeleton.assumptions.length > 0 && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-ink-soft">做了哪些假设（{skeleton.assumptions.length}）</summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-5 text-ink-soft">
            {skeleton.assumptions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function JobView({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [notFound, setNotFound] = useState(false);
  const [log, setLog] = useState<LogLine[]>([]);
  const [answer, setAnswer] = useState("");
  const [answering, setAnswering] = useState(false);
  const [answerError, setAnswerError] = useState<string | null>(null);
  const [answered, setAnswered] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopped = useRef(false);

  const poll = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    try {
      const j = await api.getJob(jobId);
      setJob(j);
      setError(null);
      if (j.stage || j.message) {
        const line = { stage: j.stage || "", message: j.message || "" };
        setLog((prev) => {
          const last = prev[prev.length - 1];
          return last && last.stage === line.stage && last.message === line.message ? prev : [...prev, line];
        });
      }
      if (j.status === "done" || j.status === "failed") return; // 结束了，不再轮询
      if (j.status === "running") setAnswered(false);
    } catch (e) {
      if (e instanceof ApiError && e.notAvailable) {
        setUnavailable(true);
        return;
      }
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true);
        return;
      }
      setError(errorText(e)); // 网络抖动：继续轮询
    }
    if (!stopped.current) timer.current = setTimeout(poll, POLL_MS);
  }, [jobId]);

  useEffect(() => {
    stopped.current = false;
    poll();
    return () => {
      stopped.current = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [poll]);

  async function submitAnswer() {
    const t = answer.trim();
    if (!t) return;
    setAnswering(true);
    setAnswerError(null);
    try {
      await api.answerJob(jobId, t);
      setAnswer("");
      setAnswered(true); // 下一次轮询会拿到新状态
    } catch (e) {
      setAnswerError(e instanceof ApiError && e.notAvailable ? "回答问题的接口即将上线。" : errorText(e));
    } finally {
      setAnswering(false);
    }
  }

  const current = stageIndex(job?.stage);
  const status = job?.status;

  return (
    <div className="mx-auto max-w-2xl px-4 pt-6 pb-16 sm:pt-10">
      <Link href="/" className="text-sm text-ink-soft hover:text-ink">
        ← 我的行程
      </Link>
      <h1 className="mt-2 text-2xl font-semibold">生成行程</h1>
      <p className="mt-1 text-sm text-ink-faint">任务 {jobId}</p>

      <div className="mt-6 space-y-4">
        {unavailable && (
          <ComingSoon title="生成进度功能即将上线">后端还没有开放任务进度接口。上线后这里会实时显示每个阶段的进度。</ComingSoon>
        )}
        {notFound && (
          <ErrorBox>
            找不到这个任务，可能已经过期。<Link href="/new" className="underline">重新填写问卷</Link>
          </ErrorBox>
        )}
        {error && !unavailable && !notFound && <ErrorBox>{error}（会自动重试）</ErrorBox>}

        {!job && !unavailable && !notFound && !error && (
          <div className="flex items-center gap-2 py-8 text-ink-soft">
            <Spinner /> 正在读取进度…
          </div>
        )}

        {job && (
          <div className="rounded-xl border border-line bg-white p-4 sm:p-5">
            <div className="flex items-center gap-3">
              {status === "running" && <Spinner className="h-5 w-5 text-accent" />}
              <div className="min-w-0">
                <div className="text-[17px] font-semibold">
                  {status === "running" && "正在生成…"}
                  {status === "needs_input" && "需要你补充信息"}
                  {status === "done" && "行程已生成"}
                  {status === "failed" && "生成失败"}
                </div>
                {job.message && <div className="mt-0.5 text-[15px] text-ink-soft">{job.message}</div>}
              </div>
            </div>

            <ol className="mt-4 grid grid-cols-4 gap-1.5 sm:grid-cols-8">
              {STAGES.map((s, i) => {
                const done = status === "done" || i < current;
                const now = i === current && status !== "done";
                return (
                  <li key={s.code} className="min-w-0">
                    <div
                      className={cx(
                        "h-1.5 rounded-full",
                        done ? "bg-accent" : now ? (status === "failed" ? "bg-red-500" : "animate-pulse bg-accent/50") : "bg-gray-200",
                      )}
                    />
                    <div className={cx("mt-1 truncate text-xs", now ? "font-medium text-ink" : "text-ink-faint")}>{s.label}</div>
                  </li>
                );
              })}
            </ol>

            {log.length > 1 && (
              <details className="mt-4 text-sm">
                <summary className="cursor-pointer text-ink-soft">进度记录（{log.length}）</summary>
                <ul className="mt-2 space-y-1 text-ink-soft">
                  {log.map((l, i) => (
                    <li key={i}>
                      {l.stage && <span className="mr-1.5 text-ink-faint">{l.stage}</span>}
                      {l.message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        {job?.status === "needs_input" && job.skeleton && <SkeletonCard skeleton={job.skeleton} />}

        {job?.status === "needs_input" && (
          <div className="rounded-xl border border-accent-line bg-white p-4 sm:p-5">
            <div className="text-[15px] font-semibold">猪猪想确认几件事</div>
            {job.questions && job.questions.length > 0 ? (
              <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-[15px]">
                {job.questions.map((q, i) => (
                  <li key={i}>{questionText(q)}</li>
                ))}
              </ol>
            ) : (
              <p className="mt-1 text-ink-soft">{job.message || "请补充信息。"}</p>
            )}
            {answered ? (
              <p className="mt-3 flex items-center gap-2 text-sm text-ink-soft">
                <Spinner className="h-4 w-4" /> 已提交，正在继续…
              </p>
            ) : (
              <div className="mt-3 space-y-2">
                <textarea
                  className={cx(inputClass, "min-h-24")}
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  placeholder="直接用一段话回答上面的问题"
                  aria-label="回答"
                />
                {answerError && <ErrorBox>{answerError}</ErrorBox>}
                <Button variant="primary" onClick={submitAnswer} busy={answering} disabled={answering || !answer.trim()}>
                  提交回答
                </Button>
              </div>
            )}
          </div>
        )}

        {job?.status === "done" && job.trip_id && (
          <ButtonLink href={`/trips/${encodeURIComponent(job.trip_id)}`} variant="primary" className="w-full sm:w-auto sm:px-6">
            查看行程 →
          </ButtonLink>
        )}
        {job?.status === "failed" && (
          <ButtonLink href="/new" className="w-full sm:w-auto">
            回到问卷修改
          </ButtonLink>
        )}
      </div>
    </div>
  );
}
