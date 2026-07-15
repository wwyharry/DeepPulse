export default function MarketPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">行情中心</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">📈 大盘概览</h3>
          <p className="text-sm text-gray-400">上证指数、深证成指、创业板指实时行情</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">🔥 板块热力图</h3>
          <p className="text-sm text-gray-400">行业/概念板块涨跌排名</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">🚀 涨停分析</h3>
          <p className="text-sm text-gray-400">涨停池、连板股、情绪周期</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
      </div>
    </div>
  );
}
