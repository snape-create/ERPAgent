/**
 * Markdown 归一化工具
 *
 * LLM 生成的内容经常出现"看起来像 Markdown、但不符合规范"的写法，
 * 例如：`##标题`（# 后缺空格）、`-项目`（- 后缺空格）、
 * 多个标题/列表项挤在同一行等。这些会被 react-markdown 当成纯文本原样输出。
 *
 * 这里做一层保守的修复，让这些内容也能正常渲染，同时尽量不破坏
 * 合法内容（URL 中的 #、负数、独立的 --- 等）。
 *
 * 重要约束：
 * 修复逻辑会"插入换行"，如果作用在表格行上，会把 GFM 表头分隔行
 * `|---|---|` 里的 `---` 误判为水平分割线，导致整张表格退化为纯文本。
 * 因此这里改为逐行处理，围栏代码块内部与表格行原样保留。
 */

const CJK_PUNCT = "\u3000-\u303f\uff00-\uffef\u4e00-\u9fff";

/** 中文汉字或常见中文标点 */
const CJK_RE = `[${CJK_PUNCT}]`;

/** 围栏代码块标记：``` 或 ~~~（行首，允许缩进） */
const FENCE_RE = /^\s*(`{3,}|~{3,})/;

/**
 * 是否为表格行。
 * 只要本行出现未转义的 `|`，就认为它属于表格（含 `|---|` 分隔行），
 * 归一化时整行跳过。
 */
function isTableLine(line: string): boolean {
  return /(^|[^\\])\|/.test(line);
}

/** 拆分表格行的单元格 */
function splitCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** 是否为 GFM 表格分隔行（`|---|---|`、`|:--|--:|` 等） */
function isDelimiterRow(line: string): boolean {
  const cells = splitCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell));
}

/** 数学公式占位符，避免归一化规则改写公式内部内容 */
const MATH_TOKEN = "\u0000MATH";

/**
 * 把"单行独占的 `$$...$$`"展开成真正的块级公式。
 *
 * remark-math 的块级语法要求 `$$` 独占一行，而 LLM 几乎总是把公式写成
 * 一行 `$$...$$`，这会被解析成行内公式（跟着文字走、不居中）。
 */
function normalizeDisplayMath(md: string): string {
  return md.replace(
    /^([ \t]*)\$\$([^\n$]+)\$\$[ \t]*$/gm,
    (_match, indent: string, body: string) =>
      `${indent}$$\n${indent}${body.trim()}\n${indent}$$`
  );
}

/**
 * 把 `$$...$$` / `$...$` 从文本中摘出来，替换成占位符。
 *
 * 公式里经常出现 `-`、`#`、`|`、反引号等字符，会被下面的"挤行修复"规则
 * 误伤（例如 `\left| x \right|` 被当成表格行）。摘出来后再放回去。
 */
function protectMath(md: string): { text: string; blocks: string[] } {
  const blocks: string[] = [];
  const store = (match: string) => {
    blocks.push(match);
    return `${MATH_TOKEN}${blocks.length - 1}\u0000`;
  };

  // 块级公式，允许跨行
  let text = md.replace(/\$\$[\s\S]*?\$\$/g, store);
  // 行内公式，不跨行
  text = text.replace(/\$[^$\n]+\$/g, store);

  return { text, blocks };
}

function restoreMath(md: string, blocks: string[]): string {
  if (!blocks.length) return md;
  return md.replace(
    new RegExp(`${MATH_TOKEN}(\\d+)\\u0000`, "g"),
    (_match, index: string) => blocks[Number(index)] ?? ""
  );
}

/** 表格单元格里混入的标题标记：`| ## 三种主流变体 |` -> `| 三种主流变体 |` */
function stripHeadingInCells(line: string): string {
  return line.replace(/(\|[ \t]*)#{1,6}[ \t]*/g, "$1");
}

/**
 * 校正表格分隔行的列数。
 * LLM 经常写出"表头 3 列、分隔行只有 2 列"的表格，GFM 会直接放弃解析，
 * 整张表退化成带竖线的普通文本。这里以表头列数为准重建分隔行。
 */
function repairTableBlock(block: string[]): string[] {
  if (block.length < 2 || !isDelimiterRow(block[1])) return block.map(stripHeadingInCells);

  const headerColumns = splitCells(block[0]).length;
  const alignments = splitCells(block[1]).map((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return ":---:";
    if (left) return ":---";
    if (right) return "---:";
    return "---";
  });

  const delimiterCells: string[] = [];
  for (let i = 0; i < headerColumns; i++) {
    delimiterCells.push(alignments[i] ?? "---");
  }

  return [
    stripHeadingInCells(block[0]),
    `| ${delimiterCells.join(" | ")} |`,
    ...block.slice(2).map(stripHeadingInCells),
  ];
}

/**
 * 对单行应用修复。
 * 只处理"行内挤行"这类问题，所有替换都可能引入换行，故仅用于非表格/非代码行。
 */
function normalizeLine(line: string): string {
  let s = line;

  // 0. 行内出现的代码围栏无法构成代码块，去掉标记降级为普通文本，
  //    否则未闭合的围栏会把后续所有内容吞进代码块
  //    例：`格式如下 👇```"` -> `格式如下 👇"`
  s = s.replace(/([^\n`])(`{3,})/g, "$1");

  // 1. 行首标题补空格：`##标题` -> `## 标题`（仅行首，避免破坏 URL 里的 #）
  s = s.replace(/^([ \t]*)(#{1,6})([^\s#])/, "$1$2 $3");

  // 1.1 行首圆点/方块符号统一成列表标记：`• 项目` -> `- 项目`
  s = s.replace(/^([ \t]*)[•·▪▫‣◦∙●○][ \t]*/, "$1- ");

  // 2. 中文/标点后紧跟标题（挤在同一行）：拆到新行并补空格
  //    例：`…合作状态##2️⃣零部件…` -> `…合作状态\n\n## 2️⃣零部件…`
  s = s.replace(
    new RegExp(`(${CJK_RE})(#{1,6})([^\\s#])`, "g"),
    "$1\n\n$2 $3"
  );

  // 3. 行首列表标记补空格：`-项目` -> `- 项目`（避免破坏负数 `-1`、`---`）
  s = s.replace(/^([ \t]*)([-*+])([^\s\-*+])/, "$1$2 $3");

  // 4. 中文/标点后紧跟 `-中文`，视为列表项：`…供应商-按名称…` -> `…供应商\n- 按名称…`
  s = s.replace(
    new RegExp(`(${CJK_RE})(-)([${CJK_PUNCT}])`, "g"),
    "$1\n$2 $3"
  );

  // 5. 拆出被文字夹住的水平分割线：`Skill---` -> `Skill\n\n---`
  //    `|` 与 `-` 出现在首尾字符时一律跳过，避免再次切开表格分隔行
  s = s.replace(/([^\n|-])-{3,}([^\n|-])/g, "$1\n\n---\n\n$2");
  s = s.replace(/([^\n|-])-{3,}$/, "$1\n\n---");

  // 6. 紧邻的加粗标记（`**A****B**`）会被 Markdown 判为非法边界，
  //    整段 `**` 原样输出。插入空格拆成 `**A** **B**`。
  //    只处理 4 个及以上连续星号，不影响 `***粗斜体***`。
  s = s.replace(/\*{4,}/g, "** **");

  return s;
}

export function normalizeMarkdown(md: string): string {
  if (!md) return md;

  // 单行 `$$...$$` 先展开成块级形式，再摘出来保护
  const { text: protectedText, blocks } = protectMath(normalizeDisplayMath(md));

  const lines = protectedText.split("\n");
  const out: string[] = [];
  let inFence = false;
  let fenceChar = "";
  // 连续的表格行先攒起来整块校正，避免逐行处理破坏分隔行
  let tableBuffer: string[] = [];

  const flushTable = () => {
    if (!tableBuffer.length) return;
    out.push(...repairTableBlock(tableBuffer));
    tableBuffer = [];
  };

  for (const raw of lines) {
    const fence = raw.match(FENCE_RE);

    if (fence) {
      flushTable();
      const ch = fence[1][0];
      if (!inFence) {
        inFence = true;
        fenceChar = ch;
      } else if (ch === fenceChar) {
        inFence = false;
      }
      out.push(raw);
      continue;
    }

    // 代码块内部原样输出，不做任何改写
    if (inFence) {
      flushTable();
      out.push(raw);
      continue;
    }

    if (isTableLine(raw)) {
      tableBuffer.push(raw);
      continue;
    }

    flushTable();
    out.push(normalizeLine(raw));
  }

  flushTable();

  return restoreMath(out.join("\n"), blocks);
}
