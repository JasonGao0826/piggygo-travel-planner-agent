"use client";

import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkCjkFriendly from "remark-cjk-friendly/parseOnly";
import remarkGfm from "remark-gfm";

/**
 * 猪猪回复是 Markdown（含表格）。表格放进可横向滚动的容器里。
 * remark-cjk-friendly：标准 Markdown 里"**加粗。**后面紧跟中文"不算加粗、会露出星号，这个插件按中文习惯处理。
 * 不显示图片：回复里的图片地址会让浏览器自动去访问别的网站（可能被人借此把聊天内容带出去），只显示图片说明文字。
 */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCjkFriendly]}
        urlTransform={(url, key) => (key === "src" ? "" : defaultUrlTransform(url))}
        components={{
          img: ({ alt }) => (alt ? <span className="text-ink-faint">[图片：{alt}]</span> : null),
          table: ({ children }) => (
            <div className="scroll-thin overflow-x-auto">
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
