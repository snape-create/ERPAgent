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
  // 后端阶段性事件可能发 result；前端展示层会映射成 done
  phase: "thinking" | "planning" | "executing" | "reviewing" | "result" | "done";
  label: string;
}

/**
 * 模型思考过程。与 token 分开是有意的：原始 CoT 不进入正文（content），
 * 前端目前只把它当作"仍在活动"的信号，不展示也不落库。
 */
export interface SSEReasoningEvent {
  type: "reasoning";
  content: string;
  source?: "main" | "analyst" | "order";
}

/**
 * 服务端在流内显式报错。与 onError（网络/连接层）区分开：
 * 前者带着可读原因，收到即代表这一轮已经失败，不需要再等 done。
 */
export interface SSEErrorEvent {
  type: "error";
  message: string;
}

/**
 * 终稿分界：此下标之后的 text 才是给用户看的最终稿，
 * 之前的正文都是草稿 / 过程文本。
 */
export interface SSERevisionEvent {
  type: "revision";
  finalStartIndex: number;
  /**
   * 第一次修订发生时，草稿在 parts 中的结束位置。
   * 前端用它锁定"第一版草稿"，修订期间答案区不回退成空。
   */
  firstRevisionIndex?: number;
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
  | SSEPhaseEvent
  | SSEReasoningEvent
  | SSEErrorEvent
  | SSERevisionEvent;

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
  /** 工具结束（成功/失败）的时间戳；用于计算耗时 */
  endedAt?: number;
  /** 结果的单行摘要，列表折叠时展示，避免把长结果塞进标题 */
  summary?: string;
}

/**
 * 消息的有序分段。content/toolCalls 是聚合视图（兼容旧组件与历史数据），
 * parts 保留真实发生顺序——正文与工具交替出现时，只有它能把顺序还原。
 * 声明在 ToolCallInfo 之后：类型引用必须先存在。
 */
export type MessagePart =
  | { type: "text"; text: string }
  | { type: "tool"; call: ToolCallInfo };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  source?: string;
  toolCalls?: ToolCallInfo[];
  /** 可选：旧历史消息没有分段信息，UI 需回退到 content/toolCalls */
  parts?: MessagePart[];
  timestamp: number;
  /** 整轮开始/结束时间（毫秒时间戳），用于消息底部"用时" */
  startedAt?: number;
  endedAt?: number;
  /**
   * parts 里的终稿起点：该下标及其之后的 text 是最终稿，
   * 之前的 text 都是草稿 / 过程文本。旧消息缺省按 0 处理。
   */
  finalStartIndex?: number;
  /**
   * 第一次修订发生时草稿的结束位置：修订期间答案区继续展示这段草稿，
   * 等终稿完成后再一次性替换。旧消息缺省按 0 处理。
   */
  firstRevisionIndex?: number;
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
