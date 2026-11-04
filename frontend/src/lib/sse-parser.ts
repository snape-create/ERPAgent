import { SSEEvent } from "./types";

/**
 * SSE 流解析器
 * 将原始文本块解析为结构化的 SSE 事件
 */
export class SSEParser {
  private buffer: string = "";

  /**
   * 输入原始文本块，返回解析出的事件数组
   */
  parse(chunk: string): SSEEvent[] {
    this.buffer += chunk;
    const events: SSEEvent[] = [];

    // SSE 事件以空行分隔。按整块解析，避免 event:/data: 被网络分片拆开后丢事件。
    const blocks = this.buffer.split(/\r?\n\r?\n/);
    // 最后一块可能不完整，留到下一次
    this.buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      if (!block.trim()) continue;

      let eventType = "message";
      const dataLines: string[] = [];

      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith(":")) continue; // SSE 注释行
        if (line.startsWith("event:")) {
          eventType = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dataLines.push(line.slice(5).replace(/^ /, ""));
        }
      }

      if (dataLines.length === 0) continue;
      const rawData = dataLines.join("\n");

      try {
        const parsed = JSON.parse(rawData);
        events.push({ type: eventType, ...parsed } as SSEEvent);
      } catch {
        // 非 JSON data，构造简单 token 事件
        if (eventType === "token") {
          events.push({
            type: "token",
            content: rawData,
            source: "main",
          } as SSEEvent);
        }
      }
    }

    return events;
  }

  reset(): void {
    this.buffer = "";
  }
}
