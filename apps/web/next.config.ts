import type { NextConfig } from "next";
import path from "node:path";

// 上线时网页和后端在同一个网址下：浏览器请求 /api/...，由 Next.js 转给容器里的后端。
// 这样登录 cookie 是同一个网站的，也不用处理跨域。本地开发不设 BACKEND_URL，网页直接请求 8765 端口。
const backend = process.env.BACKEND_URL?.replace(/\/+$/, "");

// 上线后的内容安全策略：只从自己的网址加载脚本、样式、图片和数据，不能被别的网站嵌入。
// Next.js 需要内联脚本和样式，所以有 'unsafe-inline'。只在上线（设了 BACKEND_URL，网页和后端同一个网址）时加：
// 本地 npm run build 时网页还要去 8765 端口请求后端，加了会被挡住。
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  // 应用在仓库的子目录里：明确根目录，避免 Next.js 往上层目录找别的 lockfile。
  outputFileTracingRoot: path.join(__dirname),
  // Docker 里打包成独立的 server.js，不需要整个 node_modules
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  // 本地按上线方式试运行时，换个目录，不影响正在跑的开发服务器
  distDir: process.env.NEXT_DIST_DIR || ".next",
  async rewrites() {
    return backend ? [{ source: "/api/:path*", destination: `${backend}/api/:path*` }] : [];
  },
  async headers() {
    // 页面不能被别的网站嵌进去（防止诱导点击"重新生成链接"之类的按钮）；浏览器不猜文件类型
    return [
      {
        // 只管页面；/api 开头的是后端的响应，安全头由后端自己加（凭证 PDF 要能在浏览器里直接打开）
        source: "/((?!api/).*)",
        headers: [
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "same-origin" },
          ...(backend ? [{ key: "Content-Security-Policy", value: CSP }] : []),
        ],
      },
    ];
  },
  experimental: {
    // 生成行程、和猪猪对话可能要一两分钟，代理不能太早断开
    proxyTimeout: 300_000,
    // 转给后端的请求体上限：要比后端的上传上限（10MB）加上表单本身大，否则大文件会被截断、一直等到超时
    middlewareClientMaxBodySize: "16mb", // 和后端 security.py 的 MAX_DRAIN 一样
  },
};

export default nextConfig;
