/**
 * 产物链接的统一规则。
 *
 * 模型回复里的文件链接可能写成相对路径、绝对路径，甚至带 http(s) 主机。
 * 这里统一收敛成同源的 `/api/download/...` 或 `/api/artifacts/...`：
 * - 不信任外部 host，避免把模型输出变成任意站点的跳转入口；
 * - 下载链接统一补 `download=1`，由网关返回 attachment；
 * - 预览走前端 `/view?href=...`，浏览器看到的是渲染后的页面，而不是原始 Markdown。
 */

const ARTIFACT_PREFIXES = ["/api/download/", "/api/artifacts/"] as const;

const TEXT_EXTS = new Set(["md", "markdown", "txt", "csv", "json", "log"]);
const MARKDOWN_EXTS = new Set(["md", "markdown"]);
const HTML_EXTS = new Set(["html", "htm"]);
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);

function parseArtifactUrl(href: string): URL | null {
  let url: URL;
  try {
    // 故意不用 window.location：这段代码也会在 SSR 渲染 Markdown 时执行。
    // 相对路径先补一个占位 origin，最后只保留 path/query/hash。
    url = new URL(href, "http://artifact.local");
  } catch {
    return null;
  }

  if (!ARTIFACT_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))) {
    return null;
  }
  // URL 会规范化 `..`，这里再挡一次显式路径穿越
  if (url.pathname.split("/").some((segment) => segment === "..")) {
    return null;
  }
  return url;
}

/** 把任意产物链接收敛成同源相对路径；不是产物链接返回 null。 */
export function artifactPathFromHref(href?: string): string | null {
  if (!href) return null;
  const url = parseArtifactUrl(href);
  if (!url) return null;
  return `${url.pathname}${url.search}${url.hash}`;
}

/** 补 `download=1`，让网关返回 Content-Disposition: attachment。 */
export function withDownloadFlag(href: string): string {
  const url = parseArtifactUrl(href);
  if (!url) return href;
  url.searchParams.set("download", "1");
  return `${url.pathname}${url.search}${url.hash}`;
}

/** 前端渲染版报告页：新标签打开时使用，不再直接打开原始 .md。 */
export function viewerHref(href: string): string {
  const path = artifactPathFromHref(href) ?? href;
  return `/view?href=${encodeURIComponent(path)}`;
}

export function fileNameFromHref(href: string): string {
  const url = parseArtifactUrl(href);
  const path = url?.pathname ?? href.split("?")[0].split("#")[0];
  const raw = path.slice(path.lastIndexOf("/") + 1) || "文件";
  try {
    return decodeURIComponent(raw) || raw;
  } catch {
    return raw;
  }
}

function extensionOf(href: string): string {
  const name = fileNameFromHref(href);
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

export function isTextArtifact(href: string): boolean {
  return TEXT_EXTS.has(extensionOf(href));
}

export function isMarkdownArtifact(href: string): boolean {
  return MARKDOWN_EXTS.has(extensionOf(href));
}

export function isHtmlArtifact(href: string): boolean {
  return HTML_EXTS.has(extensionOf(href));
}

export function isImageArtifact(href: string): boolean {
  return IMAGE_EXTS.has(extensionOf(href));
}

export function isPdfArtifact(href: string): boolean {
  return extensionOf(href) === "pdf";
}
