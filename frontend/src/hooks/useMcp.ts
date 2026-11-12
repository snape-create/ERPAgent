"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getMcpServers,
  toggleMcpServer,
  McpServer,
} from "@/lib/api";

export function useMcp() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getMcpServers();
      setServers(data);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载 MCP 列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const toggle = useCallback(
    async (name: string, enabled: boolean) => {
      // 乐观更新：先改 UI，失败回滚
      setServers((prev) =>
        prev.map((s) => (s.name === name ? { ...s, enabled } : s))
      );
      setMessage(null);
      try {
        const msg = await toggleMcpServer(name, enabled);
        setMessage(msg);
        setTimeout(() => setMessage(null), 3000);
      } catch (e) {
        // 回滚
        setServers((prev) =>
          prev.map((s) => (s.name === name ? { ...s, enabled: !enabled } : s))
        );
        setError(e instanceof Error ? e.message : "切换失败");
      }
    },
    []
  );

  return { servers, loading, error, message, refresh, toggle };
}
