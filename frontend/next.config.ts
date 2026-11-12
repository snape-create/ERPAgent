import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // 必须关闭响应压缩。
  //
  // Next 的压缩中间件把 text/event-stream 也视为可压缩内容，gzip 会整条缓冲，
  // 必须等到流结束才一次性吐出，SSE 因此完全失去流式效果。
  // 实测（经 /api 代理请求 chat/stream）：
  //   Accept-Encoding: gzip      -> 1 个 chunk，2949ms 后一次性返回 9108 字节
  //   Accept-Encoding: identity  -> 59 个 chunk，103ms 起逐块到达
  // 浏览器一定发送 gzip，所以这里必须关掉，否则前端只能拿到"一次性返回"。
  //
  // 代价是 Next 自身产出的静态资源不再 gzip；本项目为本地/内网 ERP Agent，
  // 若部署到公网需要压缩，请在 nginx 等前置代理上做（并放行 text/event-stream）。
  compress: false,

  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
