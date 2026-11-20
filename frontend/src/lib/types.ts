// ===== SSE 事件类型 =====
export type HarnessPhase =
  | "idle"
  | "thinking"
  | "planning"
  | "executing"
  | "reviewing"
  | "done";
export interface SSETokenEvent {
  type: "token";
  content: string;
  source: "main" | "analyst" | "order";
}

export interface SSEToolStartEvent {
  type: "tool_start";
  name: string;
  id: string;
}

export interface SSEToolArgsEvent {
  type: "tool_args";
  args: string;
}

export interface SSEToolResultEvent {
  type: "tool_result";
  name: string;
  content: string;
  // 后端可能携带工具调用 id；缺省时前端退化为按当前运行实例 / name 匹配

  id?: string;
}

export interface SSEToolEndEvent {
  type: "tool_end";
  id: string;
}

export interface SSEInterruptEvent {
  type: "interrupt";
  interrupt_type: "hitl_approval" | "order_info_supplement";
  data: InterruptData;
}

export interface SSEDoneEvent {
  type: "done";
  thread_id: string;
  interrupted: boolean;
}

export interface SSEThinkingEvent {
  type: "thinking";
  status: "start" | "end";
}

export interface SSETodoUpdateEvent {
  type: "todo_update";
  phase?: string;
  todos?: { id: string; content: string; status: string }[];
  status_change?: string;
  // legacy fields
  tool?: string;
  args?: string;
  result?: string;
}

export interface SSEPhaseEvent {
  type: "phase";
  phase: "thinking" | "planning" | "executing" | "reviewing" | "done";
  label: string;
}

export type SSEEvent =
  | SSETokenEvent
  | SSEToolStartEvent
  | SSEToolArgsEvent
  | SSEToolResultEvent
  | SSEToolEndEvent
  | SSEInterruptEvent
  | SSEDoneEvent
  | SSEThinkingEvent
  | SSETodoUpdateEvent
  | SSEPhaseEvent;

// ===== 中断数据 =====
export interface InterruptData {
  interrupt_type: string;
  // order_info_supplement
  extracted_data?: Record<string, unknown>;
  missing_fields?: string[];
  message?: string;
  // hitl_approval
  order_data?: Record<string, unknown>;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  tool_description?: string;
  /** 一次中断里包含的写操作数量，>1 时提示用户逐条核对 */
  action_count?: number;
  /** order_update_status 专用：后端补查的当前状态，用于展示「当前 → 目标」 */
  current_status?: number | null;
  current_status_label?: string;
  target_status_label?: string;
  order_number?: string;
}

// ===== 消息类型 =====
export interface ToolCallInfo {
  id: string;
  name: string;
  args: string;
  result?: string;
  status: "running" | "done" | "error";
  /** 工具开始执行的时间戳；旧历史消息没有该字段 */
  startedAt?: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  source?: string;
  toolCalls?: ToolCallInfo[];
  timestamp: number;
}

// ===== TODO 任务列表 =====
export interface TodoItem {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "complete" | "cancelled";
}

export interface TodoState {
  items: TodoItem[];
  visible: boolean;
}

// ===== 会话历史 =====
export interface Conversation {
  thread_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

// ===== 请求/响应 =====
export interface ChatRequest {
  message: string;
  thread_id: string;
  user_id: string;
  username: string;
}

export interface ResumeRequest {
  thread_id: string;
  resume_data: Record<string, unknown>;
}

// ===== 功能卡片 =====
export type CapabilityIcon = "chart" | "cart" | "box" | "search";

export interface CapabilityCard {
  icon: CapabilityIcon;
  title: string;
  description: string;
  prompt: string;
}
