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

/**
 * 表格单元格里混入的标题标记：`| ## 三种主流变体 |` -> `| 三种主流变体 |`
 *
 * 也要处理标题写在单元格中间的情况：`| 库存 ### 单位 |` -> `| 库存 单位 |`。
 * 单元格中间的单个 `#` 多半是编号（如 `#3`），只剥两个及以上。
 */
function stripHeadingInCells(line: string): string {
  return line
    .split("|")
    .map((cell) =>
      cell
        .replace(/^[ \t]*#{1,6}[ \t]*/, "")
        .replace(/([ \t])#{2,6}[ \t]*/g, "$1")
    )
    .join("|");
}

/**
 * 标题被挤到表格行开头：`### 结论五：...2915元| 供应商 | 金额 |`
 *
 * 这一行含 `|`，会被当成表格行整行跳过归一化；GFM 又不在单元格里解析标题，
 * 于是 `###` 原样显示在表头第一格。这里把标题部分拆回独立一行。
 *
 * 返回值：heading 为 null 表示不是这种写法，为 "" 表示只有标记没有文字。
 */
function splitLeadingHeading(line: string): { heading: string | null; row: string } {
  const match = line.match(/^[ \t]*(#{1,6})[ \t]*(.*?)[ \t]*(?=\|)/);
  if (!match) return { heading: null, row: line };

  const text = match[2].trim();
  const row = line.slice(match[0].length);
  if (!row.includes("|")) return { heading: null, row: line };

  return { heading: text ? `${match[1]} ${text}` : "", row };
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

  // 2.1 行内标题前有空白但缺换行：`…供应商信息。 ## P-001查询结果` ->
  //     `…供应商信息。\n\n## P-001查询结果`
  //     规则 2 只覆盖"中文紧贴 #"的写法，而模型更常见的是中间隔一个空格，
  //     此时 `##` 不在行首，react-markdown 会当成纯文本原样输出。
  //     只处理 2 个及以上 `#`，避免误伤 `C# 7` 这类正文；URL 里的 `#`
  //     后面紧跟字符而非空白，不会命中。
  s = s.replace(/([^\n#\s])[ \t]+(#{2,6})(?=[ \t]|$)/g, "$1\n\n$2");

  // 2.2 同上但 `#` 后紧贴文字（`…信息。 ##P-001…`）：拆行同时补回标题空格，
  //     否则拆出来仍是非法 ATX 标题，react-markdown 依然原样输出。
  s = s.replace(/([^\n#\s])[ \t]+(#{2,6})([^\s#])/g, "$1\n\n$2 $3");

  // 2.3 标点后直接跟标题、且 `#` 后本身有空格：`…共 6 项。## 📦 预警清单`。
  //     规则 2 要求 `#` 后紧跟非空白，2.1/2.2 要求 `#` 前有空白，三种都没覆盖
  //     这种"前后各占一半"的写法，会漏出裸露的 `##`。
  s = s.replace(new RegExp(`(${CJK_RE})(#{1,6})(?=[ \\t])`, "g"), "$1\n\n$2");

  // 3. 行首列表标记补空格：`-项目` -> `- 项目`（避免破坏负数 `-1`、`---`）
  s = s.replace(/^([ \t]*)([-*+])([^\s\-*+])/, "$1$2 $3");

  // 4. 中文/标点后紧跟 `-中文`，视为列表项：`…供应商-按名称…` -> `…供应商\n- 按名称…`
  s = s.replace(
    new RegExp(`(${CJK_RE})(-)([${CJK_PUNCT}])`, "g"),
    "$1\n$2 $3"
  );

  // 4.1 裸 URL 后紧跟列表项/标题时拆行：`.../a.png-图2...` -> `.../a.png\n\n- 图2...`
  //     如果多个下载链接用 `-` 拼在同一行，GFM 的 autolink 会把整串识别成
  //     一个 URL，点击后请求不存在的文件。只匹配常见文件后缀，避免误伤
  //     普通 URL 中恰好出现的 `-中文`。
  s = s.replace(
    /(https?:\/\/[^\s<>"'`]+?\.(?:png|jpe?g|gif|webp|svg|bmp|pdf|csv|xlsx?|docx?|pptx?|md|html?|json|txt))-(?=[\u4e00-\u9fff])/gi,
    "$1\n\n- "
  );
  s = s.replace(
    /(https?:\/\/[^\s<>"'`]+?\.(?:png|jpe?g|gif|webp|svg|bmp|pdf|csv|xlsx?|docx?|pptx?|md|html?|json|txt))(#{1,6})(?=[\u4e00-\u9fff])/gi,
    "$1\n\n$2 "
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

/**
 * 归一化结果 LRU：切换历史会话时气泡会重新挂载，useMemo 缓存随之丢失，
 * 同一段内容每次都要重跑下面十几条正则。用原文做 key 做模块级缓存，
 * 命中时零成本；Map 的插入顺序天然实现 LRU，超量淘汰最久未用项。
 */
const NORMALIZE_CACHE_MAX = 200;
const normalizeCache = new Map<string, string>();

export function normalizeMarkdown(md: string): string {
  if (!md) return md;

  const cached = normalizeCache.get(md);
  if (cached !== undefined) {
    // 提到最新位置（最近使用），LRU 只淘汰真正冷掉的条目
    normalizeCache.delete(md);
    normalizeCache.set(md, cached);
    return cached;
  }

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
      const { heading, row } = splitLeadingHeading(raw);
      if (heading) {
        // 标题独占一行，表格从 `|` 开始重新起一段
        flushTable();
        out.push(heading);
        out.push("");
        tableBuffer.push(row);
      } else if (heading === "") {
        // 只有 `###` 没有文字，丢掉标记
        tableBuffer.push(row);
      } else {
        tableBuffer.push(raw);
      }
      continue;
    }

    flushTable();
    out.push(normalizeLine(raw));
  }

  flushTable();

  const result = restoreMath(out.join("\n"), blocks);

  normalizeCache.set(md, result);
  if (normalizeCache.size > NORMALIZE_CACHE_MAX) {
    const oldest = normalizeCache.keys().next().value;
    if (oldest !== undefined) normalizeCache.delete(oldest);
  }

  return result;
}
