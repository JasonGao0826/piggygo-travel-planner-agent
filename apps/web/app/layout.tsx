import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "小猪游记 PiggyGo", template: "%s · 小猪游记" },
  description: "交给猪猪，你负责开心出发！和同行的朋友一起规划新西兰、澳洲行程。",
  applicationName: "小猪游记",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
