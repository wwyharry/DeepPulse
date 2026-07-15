export default function PortfolioPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">我的组合</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">⭐ 自选股</h3>
          <p className="text-sm text-gray-400">管理自选股列表，设置预警</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">📋 交易记录</h3>
          <p className="text-sm text-gray-400">记录交易，分析收益</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
      </div>
    </div>
  );
}
