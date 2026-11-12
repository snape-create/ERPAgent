"use client";

import { useState } from "react";
import { AlertCircle, X, Menu } from "lucide-react";
import { ChatMessage, InterruptData, TodoItem } from "@/lib/types";
import MessageList from "./MessageList";
import WelcomeScreen from "./WelcomeScreen";
import InputBar from "./InputBar";
import ThinkingIndicator from "./ThinkingIndicator";
import TodoListPanel from "./TodoListPanel";
import HarnessPhaseBar from "./HarnessPhaseBar";
import InterruptBanner from "@/components/interrupt/InterruptBanner";

interface Props {
  messages: ChatMessage[];
  streaming: boolean;
  thinking: boolean;
  interrupted: boolean;
  interruptData: InterruptData | null;
  todoItems: TodoItem[];
  todoVisible: boolean;
  pendingQueue: string[];
  phase: string;
  phaseLabel: string;
  error: string | null;
  onDismissError: () => void;
  onOpenSidebar: () => void;
  onSend: (msg: string) => void;
  onSupplement: (text: string) => void;
  onApprove: () => void;
  onReject: () => void;
}

export default function ChatArea({
  messages,
  streaming,
  thinking,
  interrupted,
  interruptData,
  todoItems,
  todoVisible,
  pendingQueue,
  phase,
  phaseLabel,
  error,
  onDismissError,
  onOpenSidebar,
  onSend,
  onSupplement,
  onApprove,
  onReject,
}: Props) {
  const [showToolCalls, setShowToolCalls] = useState(true);
  const hasMessages = messages.length > 0;
  // 这个会话里真的出现过工具调用时才显示开关，否则拨它没有任何可见效果
  const hasToolCalls = messages.some((m) => (m.toolCalls?.length ?? 0) > 0);

  return (
    <main className="flex-1 flex flex-col min-h-0 min-w-0 bg-paper">
      {/* 错误提示条 */}
      {error && (
        <div
          role="alert"
          className="flex items-center gap-2 px-4 md:px-6 h-9 shrink-0 bg-negative-soft border-b border-hairline text-[12px] text-negative"
        >
          <AlertCircle size={14} strokeWidth={1.5} className="shrink-0" />
          <span className="min-w-0 truncate">{error}</span>
          <button
            onClick={onDismissError}
            aria-label="关闭错误提示"
            className="ml-auto shrink-0 w-5 h-5 flex items-center justify-center rounded-[4px] hover:bg-raised transition-colors duration-150 ease-quiet"
          >
            <X size={13} strokeWidth={1.5} />
          </button>
        </div>
      )}

      {/* 窄屏顶部：只放抽屉入口 */}
      <div className="md:hidden shrink-0 h-11 flex items-center gap-2 px-4 border-b border-hairline">
        <button
          onClick={onOpenSidebar}
          aria-label="打开历史会话"
          className="-ml-1.5 w-8 h-8 flex items-center justify-center rounded-ctl text-ink-2 hover:bg-raised transition-colors duration-150 ease-quiet"
        >
          <Menu size={18} strokeWidth={1.5} />
        </button>
        <span className="text-[13px] font-medium text-ink">智能采购助手</span>
      </div>

      {/* Harness 阶段指示器 */}
      <HarnessPhaseBar
        phase={phase}
        phaseLabel={phaseLabel}
        visible={streaming || phase === "done"}
      />

      {/* 消息区域 / 欢迎页 */}
      {hasMessages ? (
        <MessageList
          messages={messages}
          streaming={streaming}
          showToolCalls={showToolCalls}
        />
      ) : (
        <WelcomeScreen onPromptClick={onSend} />
      )}

      {/* 深度思考状态 */}
      {thinking && <ThinkingIndicator visible={thinking} />}

      {/* 中断交互区：即使 interruptData 缺失也要渲染兜底，避免静默空白 */}
      {interrupted && (
        <InterruptBanner
          data={interruptData ?? { interrupt_type: "" }}
          onSupplement={onSupplement}
          onApprove={onApprove}
          onReject={onReject}
        />
      )}

      {/* 任务规划：停在输入框上方，展开看步骤 */}
      <TodoListPanel items={todoItems} visible={todoVisible} />

      {/* 输入区 */}
      <InputBar
        onSend={onSend}
        disabled={streaming}
        queued={pendingQueue.length}
        showToolCalls={showToolCalls}
        onToggleToolCalls={hasMessages && hasToolCalls ? setShowToolCalls : undefined}
      />
    </main>
  );
}
