"use client";

import { useMemo, type ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkBreaks from "remark-breaks";
import rehypeKatex from "rehype-katex";
import { normalizeMarkdown } from "@/lib/markdown";
import {
  artifactPathFromHref,
  fileNameFromHref,
  withDownloadFlag,
} from "@/lib/artifact-links";

interface Props {
  children: string;
}

const TableWrap = ({ children, ...props }: ComponentProps<"table">) => (
  <div className="md-table-wrap">
    <table {...props}>{children}</table>
  </div>
);

/**
 * 图表/截图：限高 360，细边圆角，点击在原始文件里查看。
 * 用 onClick 而不是包一层 <a>，避免和 markdown 里的链接嵌套成非法结构。
 */
const MarkdownImage = ({ src, alt, ...props }: ComponentProps<"img">) => {
  const href = typeof src === "string" && src ? src : null;

  return (
    <img
      {...props}
      src={src}
      alt={alt ?? ""}
      loading="lazy"
      title="点击查看原图"
      onClick={(event) => {
        if (!href) return;
        event.preventDefault();
        event.stopPropagation();
        window.open(href, "_blank", "noopener,noreferrer");
      }}
      className="block max-w-full max-h-[360px] w-auto my-2 rounded-[8px] border border-hairline bg-surface cursor-zoom-in"
    />
  );
};

/**
 * 文件链接默认走下载。
 *
 * 之前这里会把 .md/.txt 链接拦截成预览弹窗，导致“完整报告下载”点下去显示的是
 * 报告正文。现在统一补 `download=1`，由网关返回 attachment；需要阅读渲染版时，
 * 从产物区的“预览 / 新标签打开”进入 `/view` 查看页。
 */
function MarkdownLink({ href, ...props }: ComponentProps<"a">) {
  const artifact = artifactPathFromHref(href);
  if (artifact) {
    return (
      <a
        {...props}
        href={withDownloadFlag(artifact)}
        download={fileNameFromHref(artifact)}
        rel="noopener noreferrer"
      />
    );
  }

  return <a {...props} href={href} target="_blank" rel="noopener noreferrer" />;
}

const MARKDOWN_COMPONENTS = {
  table: TableWrap,
  img: MarkdownImage,
  a: MarkdownLink,
};

export default function Markdown({ children }: Props) {
  // 归一化只在内容真正变化时执行，避免每帧重跑正则
  const normalized = useMemo(() => normalizeMarkdown(children), [children]);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
      rehypePlugins={[
        [rehypeKatex, { throwOnError: false, strict: false, output: "html" }],
      ]}
      components={MARKDOWN_COMPONENTS}
    >
      {normalized}
    </ReactMarkdown>
  );
}
