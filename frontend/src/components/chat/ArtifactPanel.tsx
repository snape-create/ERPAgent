"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type MouseEvent,
} from "react";
import {
  Braces,
  Download,
  ExternalLink,
  Eye,
  File,
  FileCode,
  FileText,
  FileType,
  Image as ImageIcon,
  Loader2,
  Paperclip,
  Table,
  X,
  type LucideIcon,
} from "lucide-react";
import type { ArtifactKind, ChatArtifact } from "@/lib/artifacts";
import { isTextArtifact, viewerHref, withDownloadFlag } from "@/lib/artifact-links";
import Markdown from "./Markdown";

interface Props {
  artifacts: ChatArtifact[];
  /** 有文件生成类工具正在运行时，给一个不会误解为“无响应”的状态 */
  generating?: boolean;
}

interface KindMeta {
  label: string;
  Icon: LucideIcon;
}

const KIND_META: Record<ArtifactKind, KindMeta> = {
  html: { label: "HTML", Icon: FileCode },
  markdown: { label: "MD", Icon: FileText },
  csv: { label: "CSV", Icon: Table },
  json: { label: "JSON", Icon: Braces },
  text: { label: "TXT", Icon: FileText },
  image: { label: "图片", Icon: ImageIcon },
  pdf: { label: "PDF", Icon: FileType },
  other: { label: "文件", Icon: File },
};

const TEXT_KINDS: ReadonlySet<ArtifactKind> = new Set([
  "markdown",
  "csv",
  "json",
  "text",
]);

const ACTION_CLASS =
  "h-6 px-1.5 flex items-center gap-1 rounded-[4px] text-[11.5px] text-ink-2 hover:bg-raised transition-colors duration-150 ease-quiet";

/**
 * “新标签打开”去哪：
 * - 文本类产物走前端 /view，渲染 Markdown/表格/文本，而不是把 .md 原文甩给浏览器；
 * - PDF/图片/其它文件直接打开原始地址；
 * - HTML 不在同源新标签执行，只允许走带 sandbox 的 iframe 预览。
 */
function newTabHref(artifact: ChatArtifact): string | null {
  if (artifact.kind === "html") return null;
  if (isTextArtifact(artifact.href)) return viewerHref(artifact.href);
  if (
    artifact.kind === "pdf" ||
    artifact.kind === "image" ||
    artifact.kind === "other"
  ) {
    return artifact.href;
  }
  return null;
}

function isTextual(kind: ArtifactKind): boolean {
  return TEXT_KINDS.has(kind);
}

/* ===== 预览弹窗 ===== */

function PreviewBody({ artifact }: { artifact: ChatArtifact }) {
  const { href, filename, kind } = artifact;
  const textual = isTextual(kind);
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(textual);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!textual) return;

    const controller = new AbortController();
    setLoading(true);
    setError("");
    setText("");

    fetch(href, { cache: "no-store", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((content) => {
        setText(content);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "未知错误");
        setLoading(false);
      });

    return () => controller.abort();
  }, [href, textual]);

  const displayText = useMemo(() => {
    if (kind !== "json") return text;
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return text;
    }
  }, [kind, text]);

  if (kind === "html") {
    // 关键安全边界：只给 allow-scripts，不给 allow-same-origin。
    // 沙箱文档因此处于不透明源，拿不到本站 cookie / storage，也不能逃逸。
    return (
      <iframe
        src={href}
        title={filename}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        className="h-[70vh] w-full border-0 bg-surface"
      />
    );
  }

  if (kind === "pdf") {
    return (
      <iframe
        src={href}
        title={filename}
        className="h-[70vh] w-full border-0 bg-surface"
      />
    );
  }

  if (kind === "image") {
    return (
      <div className="flex min-h-[240px] items-center justify-center p-4">
        {/* 同源下载链接，非任意外链 */}
        <img
          src={href}
          alt={filename}
          className="max-h-[70vh] max-w-full rounded-[8px] border border-hairline bg-surface"
        />
      </div>
    );
  }

  // markdown / csv / json / text / other
  if (loading) {
    return (
      <div className="flex items-center gap-2 px-1 py-2 text-[12px] text-ink-3">
        <Loader2 size={13} strokeWidth={1.5} className="animate-spin" />
        <span>正在加载文件内容…</span>
      </div>
    );
  }

  if (error) {
    return (
      <p className="px-1 py-2 text-[12px] text-negative">
        {error.startsWith("HTTP ")
          ? `无法加载文件内容（${error}）`
          : `无法加载文件内容：${error}`}
      </p>
    );
  }

  if (kind === "markdown") {
    return (
      <div className="markdown-body">
        <Markdown>{displayText}</Markdown>
      </div>
    );
  }

  return (
    <pre className="m-0 whitespace-pre-wrap break-words font-mono text-[12px] leading-[1.7] text-ink-2">
      {displayText}
    </pre>
  );
}

function PreviewModal({
  artifact,
  onClose,
}: {
  artifact: ChatArtifact;
  onClose: () => void;
}) {
  const meta = KIND_META[artifact.kind];
  const Icon = meta.Icon;
  const newTab = newTabHref(artifact);

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
      aria-label={`预览 ${artifact.filename}`}
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[88vh] w-full max-w-4xl flex-col overflow-hidden rounded-panel border border-hairline bg-surface shadow-modal"
        onClick={(event: MouseEvent<HTMLDivElement>) => event.stopPropagation()}
      >
        <div className="flex h-12 shrink-0 items-center justify-between border-b border-hairline px-4">
          <div className="flex min-w-0 items-center gap-2">
            <Icon size={14} strokeWidth={1.5} className="shrink-0 text-ink-3" />
            <span
              className="truncate font-mono text-[12px] text-ink-2"
              title={artifact.filename}
            >
              {artifact.filename}
            </span>
          </div>
          <div className="ml-4 flex shrink-0 items-center gap-1">
            {newTab && (
              <a
                href={newTab}
                target="_blank"
                rel="noopener noreferrer"
                className={ACTION_CLASS}
              >
                新标签打开
              </a>
            )}
            <a
              href={withDownloadFlag(artifact.href)}
              download={artifact.filename}
              className={ACTION_CLASS}
            >
              下载
            </a>
            <button
              onClick={onClose}
              aria-label="关闭预览"
              className="ml-0.5 flex h-7 w-7 items-center justify-center rounded-[4px] text-ink-3 hover:bg-raised transition-colors duration-150 ease-quiet"
            >
              <X size={15} strokeWidth={1.5} />
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <PreviewBody artifact={artifact} />
        </div>
      </div>
    </div>
  );
}

/* ===== 产物行 ===== */

function ArtifactRow({
  artifact,
  onPreview,
}: {
  artifact: ChatArtifact;
  onPreview: (artifact: ChatArtifact) => void;
}) {
  const meta = KIND_META[artifact.kind];
  const Icon = meta.Icon;
  const canPreview = artifact.kind !== "other";
  // HTML 只允许在带 sandbox 的 iframe 里预览，不在同源新标签直接执行；
  // 下载走 download 属性，避免生成的脚本拿到前端页面的 origin。
  const newTab = newTabHref(artifact);

  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 px-2.5 py-2">
      <Icon size={14} strokeWidth={1.5} className="shrink-0 text-ink-3" />
      <span
        className="min-w-0 flex-1 truncate text-[13px] text-ink"
        title={artifact.filename}
      >
        {artifact.filename}
      </span>
      <span className="shrink-0 font-mono text-[10.5px] text-ink-3">
        {meta.label}
      </span>
      {artifact.sizeLabel && (
        <span className="shrink-0 font-mono text-[10.5px] text-ink-3 tnum">
          {artifact.sizeLabel}
        </span>
      )}
      <div className="ml-auto flex shrink-0 items-center gap-0.5">
        {canPreview && (
          <button onClick={() => onPreview(artifact)} className={ACTION_CLASS}>
            <Eye size={12} strokeWidth={1.5} />
            预览
          </button>
        )}
        <a
          href={withDownloadFlag(artifact.href)}
          download={artifact.filename}
          className={ACTION_CLASS}
        >
          <Download size={12} strokeWidth={1.5} />
          下载
        </a>
        {newTab && (
          <a
            href={newTab}
            target="_blank"
            rel="noopener noreferrer"
            className={ACTION_CLASS}
          >
            <ExternalLink size={12} strokeWidth={1.5} />
            新标签
          </a>
        )}
      </div>
    </li>
  );
}

export default function ArtifactPanel({ artifacts, generating }: Props) {
  const [preview, setPreview] = useState<ChatArtifact | null>(null);
  const closePreview = useCallback(() => setPreview(null), []);

  if (artifacts.length === 0 && !generating) return null;

  return (
    <section aria-label="生成的文件">
      <div className="mb-1.5 flex items-center gap-1.5 px-0.5 text-[11px] text-ink-3">
        <Paperclip size={12} strokeWidth={1.5} className="shrink-0" />
        <span>生成文件</span>
        {artifacts.length > 0 && (
          <span className="tnum">{artifacts.length}</span>
        )}
      </div>

      {artifacts.length > 0 && (
        <ul className="m-0 list-none divide-y divide-hairline overflow-hidden rounded-[8px] border border-hairline bg-surface p-0">
          {artifacts.map((artifact) => (
            <ArtifactRow
              key={artifact.href}
              artifact={artifact}
              onPreview={setPreview}
            />
          ))}
        </ul>
      )}

      {generating && (
        <div
          role="status"
          className="flex items-center gap-1.5 px-0.5 py-1.5 text-[12px] text-ink-3"
        >
          <Loader2 size={13} strokeWidth={1.5} className="animate-spin" />
          <span>正在生成文件…</span>
        </div>
      )}

      {preview && <PreviewModal artifact={preview} onClose={closePreview} />}
    </section>
  );
}
