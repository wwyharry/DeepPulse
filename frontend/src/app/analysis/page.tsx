export default function AnalysisPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">分析工具</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <a href="/analysis/screener" className="block p-6 bg-[#1f2937] rounded-lg border border-gray-800 hover:border-gray-700 transition">
          <h3 className="text-lg font-semibold text-white mb-2">🔍 条件选股</h3>
          <p className="text-sm text-gray-400">多维度技术条件筛选股票</p>
        </a>
        <a href="/analysis/backtest" className="block p-6 bg-[#1f2937] rounded-lg border border-gray-800 hover:border-gray-700 transition">
          <h3 className="text-lg font-semibold text-white mb-2">📊 策略回测</h3>
          <p className="text-sm text-gray-400">7 种策略历史回测</p>
        </a>
        <a href="/analysis/patterns" className="block p-6 bg-[#1f2937] rounded-lg border border-gray-800 hover:border-gray-700 transition">
          <h3 className="text-lg font-semibold text-white mb-2">🎯 形态识别</h3>
          <p className="text-sm text-gray-400">K 线形态自动识别</p>
        </a>
      </div>
    </div>
  );
}
