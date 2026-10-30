"use client";

import Link from "next/link";
import { useState } from "react";
import { api, errorText, joinUrl, type AdminTrip, type InviteLink } from "@/lib/api";
import { ROLE_LABEL } from "@/lib/format";
import { CopyLink } from "./InvitePanel";
import { Button, Card, ErrorBox, Field, inputClass } from "./ui";

/** 上线后第一次用：输入管理口令 → 载入行程 → 给每个成员生成专属邀请链接。 */
export function AdminView() {
  const [token, setToken] = useState("");
  const [trips, setTrips] = useState<AdminTrip[] | null>(null);
  const [links, setLinks] = useState<Record<string, InviteLink[]>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(key: string, work: () => Promise<void>) {
    setBusy(key);
    setError(null);
    try {
      await work();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(null);
    }
  }

  const load = () => run("load", async () => setTrips(await api.adminTrips(token.trim())));
  const loadDemo = () =>
    run("demo", async () => {
      await api.adminDemo(token.trim());
      setTrips(await api.adminTrips(token.trim()));
    });
  const makeLinks = (trip: AdminTrip) => {
    if (!window.confirm(`给「${trip.title}」的每个成员生成新链接？这个行程以前的链接会全部作废。`)) return;
    run(trip.id, async () => {
      const rows = await api.adminInvites(token.trim(), trip.id);
      setLinks((l) => ({ ...l, [trip.id]: rows }));
    });
  };

  return (
    <div className="mx-auto max-w-2xl px-4 pt-8 pb-16">
      <Link href="/" className="text-sm text-ink-soft hover:text-ink">
        ← 我的行程
      </Link>
      <h1 className="mt-2 text-2xl font-semibold">管理</h1>
      <p className="mt-1 text-ink-soft">只有知道管理口令的人能用（口令在 Render 的环境变量 ADMIN_TOKEN 里）。</p>

      <Card className="mt-5 p-4 sm:p-5">
        <form
          className="flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(e) => {
            e.preventDefault();
            load();
          }}
        >
          <Field label="管理口令" className="flex-1">
            <input
              type="password"
              autoComplete="off"
              className={inputClass}
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </Field>
          <Button type="submit" variant="primary" busy={busy === "load"} disabled={!token.trim() || !!busy}>
            进入
          </Button>
        </form>
      </Card>

      {error && (
        <div className="mt-4">
          <ErrorBox>{error}</ErrorBox>
        </div>
      )}

      {trips && (
        <div className="mt-5 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">全部行程（{trips.length}）</h2>
            <Button className="ml-auto" onClick={loadDemo} busy={busy === "demo"} disabled={!!busy}>
              载入新西兰演示行程
            </Button>
          </div>
          {trips.length === 0 && <p className="text-ink-soft">还没有行程。可以先载入新西兰演示行程。</p>}
          {trips.map((trip) => (
            <Card key={trip.id} className="p-4 sm:p-5">
              <div className="flex flex-wrap items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{trip.title}</div>
                  <div className="text-sm text-ink-soft">
                    {trip.members.map((m) => `${m.name}${ROLE_LABEL[m.role] ? `（${ROLE_LABEL[m.role]}）` : ""}`).join("、")}
                  </div>
                </div>
                <Button onClick={() => makeLinks(trip)} busy={busy === trip.id} disabled={!!busy}>
                  生成全部邀请链接
                </Button>
              </div>
              {links[trip.id] && (
                <div className="mt-4 space-y-3">
                  <p className="text-sm text-ink-soft">
                    每个链接只私发给对应的人。发起人的链接你先自己打开，之后在行程的「大家」页签里也能重新生成。
                  </p>
                  {links[trip.id].map((row) => (
                    <div key={row.member_id}>
                      <div className="mb-1 text-sm font-medium">
                        {row.name ?? row.member_id}
                        {row.role && ROLE_LABEL[row.role] ? `（${ROLE_LABEL[row.role]}）` : ""}
                      </div>
                      <CopyLink url={joinUrl(row.token)} />
                    </div>
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
