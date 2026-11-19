"use client";

import { useState, useRef, useCallback } from "react";
import { ArrowUp, Paperclip, X, Loader2 } from "lucide-react";
import ToolCallToggle from "@/components/common/ToolCallToggle";
import { uploadFiles, deleteUpload, UploadResult } from "@/lib/api";

interface Props {
  onSend: (message: string) => void;
  disabled?: boolean;
  queued?: number;
  showToolCalls?: boolean;
  onToggleToolCalls?: (v: boolean) => void;
}

export default function InputBar({
  onSend,
  disabled,
  queued = 0,
  showToolCalls = true,
  onToggleToolCalls,
}: Props) {
  const [input, setInput] = useState("");
  const [uploaded, setUploaded] = useState<UploadResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleSend = useCallback(() => {
    const text = input.trim();
    // 上传未完成时不发送，避免消息里缺少正在上传的文件。
    if (uploading) return;
    // 注意：streaming 时不能拦截——此时发送的消息要进入排队队列
    if (!text && uploaded.length === 0) return;

    const parts: string[] = [];
    if (text) parts.push(text);
    if (uploaded.length > 0) {
      parts.push(
        [
          "已上传文件（沙箱路径）：",
          ...uploaded.map((f) => `- /mnt/user-data/uploads/${f.filename}`),
        ].join("\n")
      );
    }

    onSend(parts.join("\n\n"));
    setInput("");
    setUploaded([]);
    setError(null);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [input, uploaded, uploading, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // isComposing：中文输入法组词时不提交，避免 Enter 选词误发送
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    // 自动调整高度
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  };

  const handleFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    // 清空以便重复选择同一个文件
    e.target.value = "";
    if (files.length === 0) return;

    setError(null);
    setUploading(true);
    try {
      const results = await uploadFiles(files);
      setUploaded((prev) => {
        const names = new Set(prev.map((f) => f.filename));
        return [...prev, ...results.filter((r) => !names.has(r.filename))];
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };

  const handleRemove = async (filename: string) => {
    setError(null);
    try {
      await deleteUpload(filename);
      setUploaded((prev) => prev.filter((f) => f.filename !== filename));
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除失败");
    }
  };

  const canSend = uploading === false && (input.trim() !== "" || uploaded.length > 0);

  return (
    <div className="shrink-0 px-4 md:px-6 pt-2 pb-3">
      {/* 与正文列同宽，避免输入框在宽屏下拉满整行、和 760 的内容对不齐 */}
      <div className="mx-auto w-full max-w-[760px]">
        {(uploaded.length > 0 || uploading) && (
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {uploaded.map((file) => (
              <span
                key={file.filename}
                className="inline-flex items-center gap-1 h-6 pl-2 pr-1 rounded-ctl bg-raised border border-hairline text-[11px] text-ink-2"
              >
                <Paperclip size={11} strokeWidth={1.5} className="shrink-0" />
                <span className="max-w-[180px] truncate">{file.filename}</span>
                <button
                  type="button"
                  onClick={() => handleRemove(file.filename)}
                  aria-label={`移除文件 ${file.filename}`}
                  className="w-4 h-4 flex items-center justify-center rounded-[4px] text-ink-3 hover:text-negative hover:bg-hairline/40 transition-colors duration-150 ease-quiet"
                >
                  <X size={11} strokeWidth={1.5} />
                </button>
              </span>
            ))}
            {uploading && (
              <span className="inline-flex items-center gap-1 text-[11px] text-ink-3">
                <Loader2 size={12} strokeWidth={1.5} className="animate-spin" />
                <span>上传中…</span>
              </span>
            )}
          </div>
        )}

        <div className="flex items-end gap-2 bg-surface border border-hairline rounded-[12px] px-3 py-2.5 shadow-lift focus-within:border-accent/60 focus-within:ring-4 focus-within:ring-accent/10 transition-[border-color,box-shadow] duration-150 ease-quiet">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            onChange={handleFiles}
            className="hidden"
            aria-label="选择要上传的文件"
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            aria-label={uploading ? "文件上传中" : "添加附件"}
            className="shrink-0 w-8 h-9 flex items-center justify-center text-ink-3 hover:text-ink-2 hover:bg-raised/70 disabled:opacity-50 rounded-ctl transition-[color,background-color] duration-150 ease-quiet"
          >
            {uploading ? (
              <Loader2 size={18} strokeWidth={1.5} className="animate-spin" />
            ) : (
              <Paperclip size={18} strokeWidth={1.5} />
            )}
          </button>
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder={disabled ? "回答中，发送后自动排队" : "输入消息"}
            rows={1}
            className="flex-1 resize-none bg-transparent outline-none text-[14px] leading-[1.6] text-ink placeholder:text-ink-3 max-h-[160px] py-1.5"
          />
          {queued > 0 && (
            <span className="shrink-0 font-mono text-[11px] text-ink-3 tnum pb-2 whitespace-nowrap">
              {queued} 条排队
            </span>
          )}
          <button
            onClick={handleSend}
            disabled={!canSend}
            aria-label="发送"
            className="shrink-0 w-9 h-9 flex items-center justify-center bg-accent hover:bg-accent-hover active:scale-[0.97] disabled:bg-raised disabled:text-ink-3 text-white rounded-full transition-[background-color,transform] duration-150 ease-quiet"
          >
            <ArrowUp size={18} strokeWidth={1.5} />
          </button>
        </div>

        {error && (
          <p role="alert" className="mt-1.5 px-1 text-[11px] text-negative">
            {error}
          </p>
        )}

        <div className="mt-2 flex items-center justify-between gap-3 px-1">
          {onToggleToolCalls ? (
            <ToolCallToggle show={showToolCalls} onToggle={onToggleToolCalls} />
          ) : (
            <span />
          )}
          <p className="flex items-center gap-1 text-[11px] text-ink-3">
            <kbd className="keycap">Enter</kbd>
            <span>发送</span>
            <kbd className="keycap ml-1.5">Shift</kbd>
            <span>+</span>
            <kbd className="keycap">Enter</kbd>
            <span>换行</span>
          </p>
        </div>
      </div>
    </div>
  );
}
