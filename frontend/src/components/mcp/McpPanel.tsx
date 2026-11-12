"use client";

import { useState } from "react";
import { useMcp } from "@/hooks/useMcp";
import { McpServer } from "@/lib/api";

function ServerSwitch({
  server,
  onToggle,
}: {
  server: McpServer;
  onToggle: (enabled: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={server.enabled}
      disabled={server.locked}
      onClick={() => onToggle(!server.enabled)}
      className="flex items-center justify-between w-full py-1.5 group disabled:cursor-not-allowed"
      title={server.locked ? "内置服务，不可禁用" : ""}
    >
      <span className="flex flex-col items-start min-w-0 mr-2">
        <span
          className={`text-[12px] truncate ${
            server.enabled ? "text-ink-2" : "text-ink-3"
          }`}
        >
          {server.name}
        </span>
        <span className="font-mono text-[10px] text-ink-3 tnum">
          {server.tool_count} 个工具
        </span>
      </span>
      <span
        className={`relative w-7 h-4 shrink-0 rounded-full transition-colors duration-150 ease-quiet ${
          server.enabled ? "bg-accent" : "bg-raised"
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-surface shadow-knob transition-transform duration-150 ease-quiet ${
            server.enabled ? "translate-x-3" : "translate-x-0"
          }`}
        />
      </span>
    </button>
  );
}

export default function McpPanel() {
  const [open, setOpen] = useState(false);
  const { servers, loading, error, message, refresh, toggle } = useMcp();
  const enabledCount = servers.filter((s) => s.enabled).length;

  return (
    <div className="border-t border-hairline">
      {/* 折叠头 */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-between w-full px-4 py-3 hover:bg-surface transition-colors duration-100 ease-quiet"
      >
        <span className="flex items-center gap-2">
          <span className="text-[11px] text-ink-3">MCP 服务</span>
          <span className="font-mono text-[10px] text-ink-3 tnum">
            {enabledCount}/{servers.length}
          </span>
        </span>
        <svg
          className={`w-3 h-3 text-ink-3 transition-transform duration-150 ease-quiet ${
            open ? "rotate-180" : ""
          }`}
          viewBox="0 0 12 12"
          fill="none"
        >
          <path
            d="M3 4.5L6 7.5L9 4.5"
            stroke="currentColor"
            strokeWidth="1.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>

      {/* 展开内容 */}
      {open && (
        <div className="px-4 pb-3">
          {loading && servers.length === 0 && (
            <span className="text-[11px] text-ink-3">加载中…</span>
          )}
          {error && (
            <span className="block text-[11px] text-red-600 mb-1">{error}</span>
          )}
          {message && (
            <span className="block text-[11px] text-accent mb-1">{message}</span>
          )}
          {servers.map((s) => (
            <ServerSwitch
              key={s.name}
              server={s}
              onToggle={(enabled) => void toggle(s.name, enabled)}
            />
          ))}
          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-1 text-[10px] text-ink-3 hover:text-ink-2"
          >
            刷新列表
          </button>
        </div>
      )}
    </div>
  );
}
