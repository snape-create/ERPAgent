"use client";

import { memo, useMemo } from "react";
import Markdown from "./Markdown";

const FENCE_RE = /^\s*(`{3,}|~{3,})/;
const LIST_RE = /^\s*(?:[-*+]|\d+[.)])\s+/;
/** 两个空格以上的缩进视作上一段的续行（列表的悬挂缩进就在这里） */
const INDENT_RE = /^(?:\t| {2,})\S/;
const HEADING_RE = /^\s*#{1,6}\s+/;

/**
 * 按顶层 block 切分 Markdown，供流式渲染逐块 memo。
 * 难点不在切，而在"哪些空行不能切"：切在列表中间会让有序列表重新从 1 开始，
 * 切在表格中间会直接破坏表格结构。
 */
export function splitMarkdownBlocks(text: string): string[] {
  if (!text) return [];

  const lines = text.split("\n");
  const blocks: string[] = [];
  let current: string[] = [];
  let inFence = false;
  // 记录开启围栏的字符和长度：``` 块里出现 ~~~ 只是普通内容；
  // 关闭围栏长度必须不短于开启围栏，不能把 4 个反引号误当成 3 个的关闭。
  let fenceChar = "";
  let fenceLen = 0;

  const flush = () => {
    const block = current.join("\n").trim();
    if (block) blocks.push(block);
    current = [];
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // 围栏行本身属于当前 block；围栏内的一切空行都不参与切分
    const fence = line.match(FENCE_RE);
    if (fence) {
      const char = fence[1][0];
      if (!inFence) {
        inFence = true;
        fenceChar = char;
        fenceLen = fence[1].length;
      } else if (char === fenceChar && fence[1].length >= fenceLen) {
        inFence = false;
        fenceChar = "";
        fenceLen = 0;
      }
      current.push(line);
      i += 1;
      continue;
    }

    if (inFence) {
      current.push(line);
      i += 1;
      continue;
    }

    if (trimmed !== "") {
      current.push(line);
      i += 1;
      continue;
    }

    // 空行：向后找到下一个非空行，再决定这是块内分隔还是真正的边界
    let j = i + 1;
    while (j < lines.length && lines[j].trim() === "") j += 1;
    if (j >= lines.length) break; // 尾部空行直接丢弃

    // 缩进判断必须看原始行，trim 之后前导空格就丢了
    const prevRaw = current.length > 0 ? current[current.length - 1] : "";
    const nextRaw = lines[j];
    const prev = prevRaw.trim();
    if (!prev) {
      i = j; // 开头的空行
      continue;
    }

    const next = nextRaw.trim();
    const nextStartsFence = FENCE_RE.test(next);
    // 标题一定是新的视觉段落，无论前面是什么
    const nextIsHeading = HEADING_RE.test(next);
    // 两边都至少有两个竖线才当表格行，避免把普通含 "|" 的段落粘在一起
    const pipeCount = (value: string) => (value.match(/\|/g) ?? []).length;
    const inTable = pipeCount(prev) >= 2 && pipeCount(next) >= 2;
    const listContinuation =
      (LIST_RE.test(next) || INDENT_RE.test(nextRaw)) &&
      (LIST_RE.test(prev) || INDENT_RE.test(prevRaw));

    if (!nextStartsFence && !nextIsHeading && (inTable || listContinuation)) {
      // 不切：空行直接丢掉，保持列表/表格连续
      i = j;
      continue;
    }

    flush();
    i = j;
  }

  flush();
  return blocks;
}

// 单个 block 的渲染结果与文本一一对应，前面的 block 在流式期间不会再变
const MarkdownBlock = memo(function MarkdownBlock({ text }: { text: string }) {
  return <Markdown>{text}</Markdown>;
});

interface Props {
  text: string;
}

/**
 * 流式 Markdown：把全文切成顶层 block，逐块 memo。
 * 每个 token 只让最后一段重新解析，长回答不会每次都跑整篇 ReactMarkdown。
 */
export default function StreamingMarkdown({ text }: Props) {
  const blocks = useMemo(() => splitMarkdownBlocks(text), [text]);

  return (
    <>
      {blocks.map((block, idx) => (
        // 流式只会在尾部追加，前面的 index 稳定，可以安全用作 key
        <MarkdownBlock key={idx} text={block} />
      ))}
    </>
  );
}
