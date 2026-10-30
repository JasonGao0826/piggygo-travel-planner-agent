"use client";

// 登录状态：现在是本地开发（open）还是上线后的邀请链接模式（invite），以及我在每个行程里是谁。

import { useEffect, useState } from "react";
import { ApiError, api, type SessionInfo } from "./api";

const OPEN: SessionInfo = { mode: "open", trips: [], needs_beta_code: false };

let cached: Promise<SessionInfo> | null = null;

/** 读登录状态。refresh=true 时重新问后端（刚加入、刚建好行程之后）。 */
export function loadSession(refresh = false): Promise<SessionInfo> {
  if (!cached || refresh) {
    cached = api.getSession().catch((e) => {
      cached = null;
      // 旧版后端没有这个接口：当作本地开发模式
      if (e instanceof ApiError && e.notAvailable) return OPEN;
      throw e;
    });
  }
  return cached;
}

export function useSession(): SessionInfo | null {
  const [session, setSession] = useState<SessionInfo | null>(null);
  useEffect(() => {
    let alive = true;
    loadSession()
      .then((s) => alive && setSession(s))
      .catch(() => alive && setSession(OPEN)); // 连不上后端时，各个页面自己会显示错误
    return () => {
      alive = false;
    };
  }, []);
  return session;
}

/** 邀请链接模式下，我在这个行程里的成员 id；本地开发模式返回 null。 */
export function sessionMember(session: SessionInfo | null, tripId: string): string | null {
  if (session?.mode !== "invite") return null;
  return session.trips.find((t) => t.trip_id === tripId)?.member_id ?? null;
}
