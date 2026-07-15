import type { Metadata } from 'next';
import '@/styles/globals.css';

export const metadata: Metadata = {
  title: 'DeepPulse — A 股短线分析 AI Agent',
  description: 'AI 驱动的 A 股短线分析平台，支持实时行情、技术分析、策略回测',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark">
      <body className="min-h-screen bg-[#111827] text-gray-200">
        <nav className="border-b border-gray-800 bg-[#1f2937]">
          <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4">
            <div className="flex items-center gap-6">
              <a href="/" className="text-lg font-bold text-blue-400">
                DeepPulse
              </a>
              <div className="flex items-center gap-4 text-sm">
                <a href="/chat" className="text-gray-400 hover:text-white transition">
                  AI 对话
                </a>
                <a href="/market" className="text-gray-400 hover:text-white transition">
                  行情
                </a>
                <a href="/analysis" className="text-gray-400 hover:text-white transition">
                  分析
                </a>
                <a href="/portfolio" className="text-gray-400 hover:text-white transition">
                  组合
                </a>
                <a href="/memory" className="text-gray-400 hover:text-white transition">
                  记忆
                </a>
                <a href="/strategies" className="text-gray-400 hover:text-white transition">
                  战法
                </a>
              </div>
            </div>
            <div className="text-sm text-gray-500">v0.2.2</div>
          </div>
        </nav>
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
