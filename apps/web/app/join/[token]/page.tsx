import type { Metadata } from "next";
import { JoinView } from "@/components/JoinView";

// 链接里带着登录用的 token：不要通过 Referer 发给别的网站
export const metadata: Metadata = { title: "加入行程", referrer: "no-referrer", robots: { index: false, follow: false } };

export default async function JoinPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <JoinView token={token} />;
}
