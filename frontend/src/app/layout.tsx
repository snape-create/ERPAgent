import type { Metadata } from "next";
import { IBM_Plex_Sans, IBM_Plex_Mono, Noto_Sans_SC } from "next/font/google";
import "katex/dist/katex.min.css";
import "./globals.css";

// 拉丁与数字用 Plex，中文用 Noto Sans SC；等宽只给编号和数量
const plex = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

const noto = Noto_Sans_SC({
  subsets: ["latin"],
  // 补 600：正文里 `.markdown-body strong` 用的是 600，缺这一档会被浏览器合成假粗体
  weight: ["400", "500", "600"],
  variable: "--font-noto",
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  title: "智能采购助手 - ERP Agent",
  description: "面向摩托车零部件采购的对话工作台，写操作需人工审批",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // Browser extensions may add attributes to <html> before hydration (for
    // example Immersive Translate). Suppress only this root-level mismatch;
    // keep hydration checks enabled for application content below it.
    <html
      lang="zh-CN"
      className={`${plex.variable} ${plexMono.variable} ${noto.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
