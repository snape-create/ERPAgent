"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type MouseEvent,
  type ComponentProps,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import remarkBreaks from "remark-breaks";
import rehypeKatex from "rehype-katex";
import { normalizeMarkdown } from "@/lib/markdown";

interface Props {
  children: string;
}

const DOWNLOAD_PATH = "/api/download/";
const TEXT_FILE_RE = /\.(?:md|markdown|txt|csv|json|log)(?:$|[?#])/i;
const MARKDOWN_FILE_RE = /\.(?:md|markdown)(?:$|[?#])/i;

/** 仅允许同源的下载链接做文本预览，防止 LLM 输出的链接探测内网 */
function isPreviewableText(href?: string): boolean {
  if (!href) return false;
  let url: URL;
  try {
    url = new URL(href, window.location.href);
  } catch {
    return false;
  }
  return url.origin === window.location.origin && url.pathname.includes(DOWNLOAD_PATH) && TEXT_FILE_RE.test(href);
}

function fileNameFromHref(href: string): string {
  const raw = href.split("?")[0].split("#")[0].split("/").pop() || "预览";
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

type PreviewState = {
  name: string;
  href: string;
  content: string;
};

// ===== 链接组件：内聚预览逻辑，使 components 映射可提为模块级常量 =====
function MarkdownLink({
  href,
  onPreview,
  ...props
}: ComponentProps<"a"> & { onPreview: (event: MouseEvent<HTMLAnchorElement>, href?: string) => void }) {
  return (
    <a
      {...props}
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => onPreview(event, href)}
    />
  );
}

const TableWrap = ({ children, ...props }: ComponentProps<"table">) => (
  <div className="md-table-wrap">
    <table {...props}>{children}</table>
  </div>
);

function PreviewBody({ href, content }: { href: string; content: string }) {
  const normalized = useMemo(() => normalizeMarkdown(content), [content]);
  if (MARKDOWN_FILE_RE.test(href)) {
    return (
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
        rehypePlugins={[
          [rehypeKatex, { throwOnError: false, strict: false, output: "html" }],
        ]}
        components={markdownPreviewComponents}
      >
        {normalized}
      </ReactMarkdown>
    );
  }
  return <pre className="whitespace-pre-wrap break-words text-xs">{content}</pre>;
}

const PreviewLink = (props: ComponentProps<"a">) => (
  <a {...props} target="_blank" rel="noopener noreferrer" />
);

/**
 * 图表/截图：限高 360，细边圆角，点击在原始文件里查看。
 * 用 onClick 而不是包一层 <a>，避免和 markdown 里的链接嵌套成非法结构。
 */
const MarkdownImage = ({ src, alt, ...props }: ComponentProps<"img">) => {
  // img 的 src 在类型上还可能是 Blob，只有字符串才当链接打开
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

const markdownPreviewComponents = {
  table: TableWrap,
  a: PreviewLink,
  img: MarkdownImage,
};

function PreviewModal({
  name,
  href,
  content,
  onClose,
}: {
  name: string;
  href: string;
  content: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={name}
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-panel border border-hairline bg-surface shadow-modal"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between h-12 px-4 border-b border-hairline">
          <span className="truncate font-mono text-[12px] text-ink-2">{name}</span>
          <div className="ml-4 flex shrink-0 items-center gap-1">
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="px-2 h-7 flex items-center rounded-[6px] text-[12px] text-ink-2 hover:bg-raised transition-colors duration-150 ease-quiet"
            >
              新标签打开
            </a>
            <button
              onClick={onClose}
              className="px-2 h-7 flex items-center rounded-[6px] text-[12px] text-ink-2 hover:bg-raised transition-colors duration-150 ease-quiet"
            >
              关闭
            </button>
          </div>
        </div>
        <div className="markdown-body overflow-y-auto px-5 py-4">
          <PreviewBody href={href} content={content} />
        </div>
      </div>
    </div>
  );
}

export default function Markdown({ children }: Props) {
  // 归一化只在内容真正变化时执行，避免每帧重跑正则
  const normalized = useMemo(() => normalizeMarkdown(children), [children]);
  const [preview, setPreview] = useState<PreviewState | null>(null);

  const handleLinkClick = useCallback(
    async (event: MouseEvent<HTMLAnchorElement>, href?: string) => {
      if (!isPreviewableText(href)) return;
      event.preventDefault();
      const name = fileNameFromHref(href!);
      try {
        const response = await fetch(href!, { cache: "no-store" });
        if (!response.ok) {
          const status = response.status;
          throw new Error(String(status));
        }
        const text = await response.text();
        setPreview({ name, href: href!, content: text });
      } catch (err) {
        const status = err instanceof Error ? err.message : "";
        setPreview({
          name,
          href: href!,
          content: `> 无法加载文件内容${status ? `（HTTP ${status}）` : ""}`,
        });
      }
    },
    []
  );

  // 组件内只需这一个随渲染变化的映射；table/a 本身均为稳定引用
  const components = useMemo(
    () => ({
      table: TableWrap,
      img: MarkdownImage,
      a: (props: ComponentProps<"a">) => (
        <MarkdownLink {...props} onPreview={handleLinkClick} />
      ),
    }),
    [handleLinkClick]
  );

  return (
    <>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath, remarkBreaks]}
        rehypePlugins={[
          [rehypeKatex, { throwOnError: false, strict: false, output: "html" }],
        ]}
        components={components}
      >
        {normalized}
      </ReactMarkdown>
      {preview && (
        <PreviewModal
          name={preview.name}
          href={preview.href}
          content={preview.content}
          onClose={() => setPreview(null)}
        />
      )}
    </>
  );
}
