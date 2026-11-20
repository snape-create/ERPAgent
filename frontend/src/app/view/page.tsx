"use client";

import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, Download, Loader2 } from "lucide-react";
import Markdown from "@/components/chat/Markdown";
import {
  artifactPathFromHref,
  fileNameFromHref,
  isHtmlArtifact,
  isImageArtifact,
  isMarkdownArtifact,
  isPdfArtifact,
  isTextArtifact,
  withDownloadFlag,
} from "@/lib/artifact-links";

type ViewKind = "markdown" | "json" | "text" | "html" | "pdf" | "image" | "other";

function kindOf(href: string): ViewKind {
  if (isMarkdownArtifact(href)) return "markdown";
  if (isHtmlArtifact(href)) return "html";
  if (isPdfArtifact(href)) return "pdf";
  if (isImageArtifact(href)) return "image";
  if (isTextArtifact(href)) {
    return fileNameFromHref(href).toLowerCase().endsWith(".json") ? "json" : "text";
  }
  return "other";
}

/**
 * 报告查看页。
 *
 * 浏览器不会原生渲染 .md；直接在新标签打开 `/api/artifacts/...md` 只会看到原文甚至空白。
 * 这里把同源产物 fetch 下来，用前端 Markdown 组件渲染成真正的阅读页。
 * 只接受 `/api/download/` 与 `/api/artifacts/` 前缀，避免变成任意外链代理。
 */
export default function ArtifactViewPage() {
  const [path, setPath] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<ViewKind>("other");
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requested = params.get("href") ?? "";
    const safePath = artifactPathFromHref(requested);

    if (!safePath) {
      setError("无效的文件地址");
      setLoading(false);
      return;
    }

    const fileKind = kindOf(safePath);
    const fileName = fileNameFromHref(safePath);
    setPath(safePath);
    setName(fileName);
    setKind(fileKind);
    document.title = `${fileName} · 报告查看`;

    // iframe / img 能直接渲染的类型，不需要先 fetch
    if (fileKind === "html" || fileKind === "pdf" || fileKind === "image") {
      setLoading(false);
      return;
    }
    if (fileKind === "other") {
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    fetch(safePath, { cache: "no-store", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.text();
      })
      .then((text) => {
        setContent(
          fileKind === "json"
            ? (() => {
                try {
                  return JSON.stringify(JSON.parse(text), null, 2);
                } catch {
                  return text;
                }
              })()
            : text
        );
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "文件加载失败");
        setLoading(false);
      });

    return () => controller.abort();
  }, []);

  const goBack = () => {
    if (window.history.length > 1) {
      window.history.back();
    } else {
      window.location.href = "/";
    }
  };

  return (
    <div className="min-h-dvh bg-paper text-ink">
      {/* 顶部只放返回、文件名和下载，避免查看页再出现一套复杂导航 */}
      <header className="sticky top-0 z-10 flex h-12 items-center gap-2 border-b border-hairline bg-surface/95 px-4 backdrop-blur-sm">
        <button
          type="button"
          onClick={goBack}
          aria-label="返回"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[6px] text-ink-3 hover:bg-raised hover:text-ink-2 transition-colors ease-quiet"
        >
          <ArrowLeft size={15} strokeWidth={1.5} />
        </button>
        <span className="min-w-0 flex-1 truncate font-mono text-[12px] text-ink-2" title={name}>
          {name || "报告查看"}
        </span>
        {path && (
          <a
            href={withDownloadFlag(path)}
            download={name || undefined}
            className="flex h-7 shrink-0 items-center gap-1.5 rounded-[6px] px-2 text-[12px] text-ink-2 hover:bg-raised transition-colors ease-quiet"
          >
            <Download size={13} strokeWidth={1.5} />
            下载
          </a>
        )}
      </header>

      <main className="mx-auto w-full max-w-[860px] px-4 py-6 md:px-6">
        {loading ? (
          <div className="flex items-center gap-2 py-6 text-[12px] text-ink-3">
            <Loader2 size={14} strokeWidth={1.5} className="animate-spin" />
            <span>正在加载文件…</span>
          </div>
        ) : error ? (
          <div className="flex items-start gap-2 rounded-[8px] border border-hairline bg-surface px-3 py-2 text-[12px] text-negative">
            <AlertCircle size={14} strokeWidth={1.5} className="mt-0.5 shrink-0" />
            <span>{error.startsWith("HTTP ") ? `无法加载文件（${error}）` : error}</span>
          </div>
        ) : kind === "markdown" ? (
          <article className="markdown-body">
            <Markdown>{content}</Markdown>
          </article>
        ) : kind === "json" || kind === "text" ? (
          <pre className="whitespace-pre-wrap break-words rounded-[8px] border border-hairline bg-surface p-4 font-mono text-[12px] leading-[1.7] text-ink-2">
            {content}
          </pre>
        ) : kind === "html" && path ? (
          // 不交给同源新标签直接执行：沙箱只放 allow-scripts，不给 allow-same-origin
          <iframe
            src={path}
            title={name}
            sandbox="allow-scripts"
            referrerPolicy="no-referrer"
            className="h-[80vh] w-full rounded-[8px] border border-hairline bg-surface"
          />
        ) : kind === "pdf" && path ? (
          <iframe
            src={path}
            title={name}
            className="h-[80vh] w-full rounded-[8px] border border-hairline bg-surface"
          />
        ) : kind === "image" && path ? (
          <div className="flex justify-center">
            <img
              src={path}
              alt={name}
              className="max-h-[80vh] max-w-full rounded-[8px] border border-hairline bg-surface"
            />
          </div>
        ) : (
          <div className="rounded-[8px] border border-hairline bg-surface p-4 text-[12px] text-ink-3">
            该文件类型不支持在线预览，请使用右上角下载。
          </div>
        )}
      </main>
    </div>
  );
}
