import { Conversation, ChatRequest, ResumeRequest } from "./types";

// 默认走相对路径 /api，由 next.config.ts 的 rewrites 代理到后端；
// 需要直连后端时可通过 NEXT_PUBLIC_API_BASE 覆盖。
const BASE_URL = process.env.NEXT_PUBLIC_API_BASE ?? "/api";

// ===== 对话 API =====
export async function streamChat(
  request: ChatRequest,
  onChunk: (chunk: string) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(`${BASE_URL}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });

  if (!response.ok) {
    throw new Error(`Stream failed: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
}

export async function resumeChat(
  request: ResumeRequest,
  onChunk: (chunk: string) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(
    `${BASE_URL}/chat/${request.thread_id}/resume`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume: request.resume_data }),
      signal,
    }
  );

  if (!response.ok) {
    throw new Error(`Resume failed: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onChunk(decoder.decode(value, { stream: true }));
  }
}

export async function getChatState(threadId: string) {
  const res = await fetch(`${BASE_URL}/chat/${threadId}/state`);
  if (!res.ok) throw new Error(`State failed: ${res.status}`);
  return res.json();
}

// ===== 历史 API =====
const USER_ID = "user-001";

export async function getConversations(): Promise<Conversation[]> {
  const res = await fetch(`${BASE_URL}/history?user_id=${USER_ID}`);
  if (!res.ok) throw new Error(`History failed: ${res.status}`);
  const data = await res.json();
  return data.conversations ?? [];
}

export async function getMessages(threadId: string) {
  const res = await fetch(`${BASE_URL}/history/${threadId}/messages`);
  if (!res.ok) throw new Error(`Messages failed: ${res.status}`);
  const data = await res.json();
  return data.messages ?? [];
}

export async function deleteConversation(threadId: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/history/${threadId}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

// ===== MCP 管理 API =====
export interface McpServer {
  name: string;
  description: string;
  enabled: boolean;
  locked: boolean;
  tool_count: number;
}

export async function getMcpServers(): Promise<McpServer[]> {
  const res = await fetch(`${BASE_URL}/mcp/servers`);
  if (!res.ok) throw new Error(`MCP servers failed: ${res.status}`);
  const data = await res.json();
  return data.servers ?? [];
}

export async function toggleMcpServer(
  name: string,
  enabled: boolean
): Promise<string> {
  const res = await fetch(`${BASE_URL}/mcp/servers/${name}/toggle`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  const data = await res.json();
  return data.message ?? "已应用";
}
