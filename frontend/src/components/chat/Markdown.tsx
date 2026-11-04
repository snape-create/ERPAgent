"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkBreaks from "remark-breaks";
import rehypeKatex from "rehype-katex";
import { normalizeMarkdown } from "@/lib/markdown";

interface Props {
  children: string;
}

/**
 * 统一的 Markdown 渲染组件。
 * - remark-gfm：表格、任务列表、删除线等扩展语法
 * - remark-math + rehype-katex：把 `$...$` / `$$...$$` 渲染成真实公式
 * - remark-breaks：把段落内的单个换行渲染成 <br>（LLM 习惯"一行一个意思"，
 *   而标准 Markdown 会把连续单换行合并成一整段）
 * - normalizeMarkdown：渲染前修复 LLM 常见脏格式
 * - 表格外包滚动容器，避免宽表把聊天气泡撑破
 *
 * 注意：rehype-katex 必须关掉 throwOnError —— LLM 产出的 LaTeX 经常有语法
 * 错误，一旦抛异常整个消息气泡会白屏。
 */
export default function Markdown({ children }: Props) {
  const normalized = normalizeMarkdown(children);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
      rehypePlugins={[
        [rehypeKatex, { throwOnError: false, strict: false, output: "html" }],
      ]}
      components={{
        table: ({ node: _node, ...props }) => (
          <div className="md-table-wrap">
            <table {...props} />
          </div>
        ),
        a: ({ node: _node, ...props }) => (
          <a {...props} target="_blank" rel="noopener noreferrer" />
        ),
      }}
    >
      {normalized}
    </ReactMarkdown>
  );
}
