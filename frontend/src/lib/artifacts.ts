/**
 * 聊天产物提取
 *
 * Agent 生成文件后，下载链接会出现在两个地方：
 * 1. assistant 正文里的 Markdown 链接 / 图片
 * 2. 工具结果字符串里的同款链接
 *
 * 这里把两者统一成 ChatArtifact，供消息下方的产物面板使用。
 * 约束：只接受指向本服务 /api/download/ 或 /api/artifacts/ 的链接，避免把
 * 模型输出的任意外链变成可点击的下载/预览入口；绝对地址一律去掉 host，
 * 只保留同源路径。
 */

export type ArtifactKind =
  | "html"
  | "markdown"
  | "csv"
  | "json"
  | "text"
  | "image"
  | "pdf"
  | "other";

export interface ChatArtifact {
  href: string;
  filename: string;
  kind: ArtifactKind;
  sizeLabel?: string;
}

const ARTIFACT_PREFIXES = ["/api/download/", "/api/artifacts/"] as const;

const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"]);
const MARKDOWN_EXTS = new Set(["md", "markdown"]);
const TEXT_EXTS = new Set(["txt", "log"]);

// Markdown 链接/图片：`[文字](url)`、`![alt](url)`，允许可选的 title
const MD_IMAGE_RE = /!\[[^\]]*\]\(\s*<?([^)>\s]+)>?(?:\s+["'][^"']*["'])?\s*\)/g;
const MD_LINK_RE = /\[[^\]]*\]\(\s*<?([^)>\s]+)>?(?:\s+["'][^"']*["'])?\s*\)/g;
// 裸 URL；排除括号与方括号，避免把 Markdown 的收尾符号吞进地址
const BARE_ABSOLUTE_RE = /https?:\/\/[^\s<>"'`()\[\]]+/gi;
const BARE_RELATIVE_RE =
  /(?:^|[\s(（"'`:：，。；、])(\/api\/(?:download|artifacts)\/[^\s<>"'`()\[\]]+)/g;
// `文件大小: 16.9 KB` / `文件大小：16.9 KB`
const SIZE_RE = /文件大小\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*(B|KB|MB|GB)/gi;

interface RawMatch {
  href: string;
  index: number;
}

interface RawSize {
  index: number;
  label: string;
}

/**
 * 把链接收敛成同源相对路径。只认 `/api/download/` 或 `/api/artifacts/`
 * 开头的路径；`http://localhost:8000/api/download/x.html` 之类会丢掉
 * protocol + host。任何其他 host / protocol 都返回 null。
 */
function normalizeDownloadHref(raw: string): string | null {
  let value = raw.trim();
  // Markdown 自动链接可能写成 <url>
  if (value.startsWith("<") && value.endsWith(">")) {
    value = value.slice(1, -1).trim();
  }
  // 正文里裸链接后面常跟中文/英文标点或收尾括号
  value = value.replace(/[.,;:!?，。；：！？、）)\]}>」』】]+$/, "");
  if (!value) return null;

  let path: string;
  const prefix = ARTIFACT_PREFIXES.find((item) => value.startsWith(item));
  if (prefix) {
    path = value;
  } else {
    const absolute =
      /^https?:\/\/[^/?#]+(\/api\/(?:download|artifacts)\/[^\s]*)$/i.exec(value);
    if (!absolute) return null;
    path = absolute[1];
  }

  // 查询参数保留（下载接口目前不用，但不能擅自丢掉）；hash 对请求无意义
  const hashIndex = path.indexOf("#");
  if (hashIndex >= 0) path = path.slice(0, hashIndex);

  if (!ARTIFACT_PREFIXES.some((item) => path.startsWith(item))) return null;
  if (/\s/.test(path)) return null;
  // 不让 ../ 把请求带出下载前缀
  if (path.split("/").some((segment) => segment === "..")) return null;

  return path;
}

function filenameFromHref(href: string): string {
  const path = href.split("#")[0].split("?")[0];
  const raw = path.slice(path.lastIndexOf("/") + 1);
  if (!raw) return "文件";
  try {
    const decoded = decodeURIComponent(raw);
    return decoded || raw;
  } catch {
    return raw;
  }
}

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(dot + 1).toLowerCase() : "";
}

function kindOf(filename: string): ArtifactKind {
  const ext = extensionOf(filename);
  if (ext === "html" || ext === "htm") return "html";
  if (MARKDOWN_EXTS.has(ext)) return "markdown";
  if (ext === "csv") return "csv";
  if (ext === "json") return "json";
  if (TEXT_EXTS.has(ext)) return "text";
  if (IMAGE_EXTS.has(ext)) return "image";
  if (ext === "pdf") return "pdf";
  return "other";
}

function toArtifact(href: string): ChatArtifact {
  const filename = filenameFromHref(href);
  return { href, filename, kind: kindOf(filename) };
}

function collectMatches(text: string): RawMatch[] {
  const matches: RawMatch[] = [];

  const push = (raw: string, index: number) => {
    const href = normalizeDownloadHref(raw);
    if (href) matches.push({ href, index });
  };

  MD_IMAGE_RE.lastIndex = 0;
  for (let m = MD_IMAGE_RE.exec(text); m; m = MD_IMAGE_RE.exec(text)) {
    if (m[1]) push(m[1], m.index);
  }

  MD_LINK_RE.lastIndex = 0;
  for (let m = MD_LINK_RE.exec(text); m; m = MD_LINK_RE.exec(text)) {
    if (m[1]) push(m[1], m.index);
  }

  BARE_ABSOLUTE_RE.lastIndex = 0;
  for (let m = BARE_ABSOLUTE_RE.exec(text); m; m = BARE_ABSOLUTE_RE.exec(text)) {
    push(m[0], m.index);
  }

  BARE_RELATIVE_RE.lastIndex = 0;
  for (let m = BARE_RELATIVE_RE.exec(text); m; m = BARE_RELATIVE_RE.exec(text)) {
    const raw = m[1];
    if (!raw) continue;
    // 捕获组前面还有一个边界字符，需要把偏移补回来
    push(raw, m.index + m[0].indexOf(raw));
  }

  // 链接语法和裸 URL 会命中同一地址：先按出现顺序排好，同一 href 只留
  // 最早的一条，否则「文件大小」会挂到重复项上，去重时反而丢失。
  const seen = new Set<string>();
  return matches
    .sort((a, b) => a.index - b.index)
    .filter((match) => {
      if (seen.has(match.href)) return false;
      seen.add(match.href);
      return true;
    });
}

function collectSizes(text: string): RawSize[] {
  const sizes: RawSize[] = [];
  SIZE_RE.lastIndex = 0;
  for (let m = SIZE_RE.exec(text); m; m = SIZE_RE.exec(text)) {
    const number = m[1];
    const unit = m[2].toUpperCase();
    sizes.push({ index: m.index, label: `${number} ${unit}` });
  }
  return sizes;
}

function dedupe(artifacts: ChatArtifact[]): ChatArtifact[] {
  const byHref = new Map<string, ChatArtifact>();
  for (const artifact of artifacts) {
    const existing = byHref.get(artifact.href);
    if (!existing) {
      byHref.set(artifact.href, { ...artifact });
    } else if (!existing.sizeLabel && artifact.sizeLabel) {
      existing.sizeLabel = artifact.sizeLabel;
    }
  }
  return [...byHref.values()];
}

/**
 * 从一段文本中提取产物。大小会挂到它前面最近的一条产物上，
 * 对应工具结果里「链接一行、文件大小一行」的排版。
 */
export function extractArtifactsFromText(text: string): ChatArtifact[] {
  if (!text) return [];

  const matches = collectMatches(text);
  const artifacts = matches.map((match) => toArtifact(match.href));
  const sizes = collectSizes(text);

  // 数量一致时按出现顺序一一对应（多个文件一起导出）；
  // 否则把大小给位置最近的那条产物，兼容「先写大小、后写链接」的文案。
  if (sizes.length > 0 && sizes.length === artifacts.length) {
    sizes.forEach((size, index) => {
      artifacts[index].sizeLabel = size.label;
    });
  } else {
    for (const size of sizes) {
      let best = -1;
      let bestDistance = Number.POSITIVE_INFINITY;
      for (let i = 0; i < matches.length; i++) {
        if (artifacts[i].sizeLabel) continue;
        const distance = Math.abs(matches[i].index - size.index);
        if (distance < bestDistance) {
          best = i;
          bestDistance = distance;
        }
      }
      if (best >= 0) artifacts[best].sizeLabel = size.label;
    }
  }

  return dedupe(artifacts);
}

export function extractArtifactsFromToolCalls(
  toolCalls: Array<{ name: string; result?: string }>
): ChatArtifact[] {
  const all: ChatArtifact[] = [];
  for (const call of toolCalls) {
    if (!call.result) continue;
    all.push(...extractArtifactsFromText(call.result));
  }
  return dedupe(all);
}

export function extractChatArtifacts(message: {
  content: string;
  toolCalls?: Array<{ name: string; result?: string }>;
}): ChatArtifact[] {
  return dedupe([
    ...extractArtifactsFromText(message.content ?? ""),
    ...extractArtifactsFromToolCalls(message.toolCalls ?? []),
  ]);
}

export function hasChatArtifact(message: {
  content: string;
  toolCalls?: Array<{ name: string; result?: string }>;
}): boolean {
  return extractChatArtifacts(message).length > 0;
}
