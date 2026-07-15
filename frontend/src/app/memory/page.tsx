export default function MemoryPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">记忆中心</h1>
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">🎯 预测追踪</h3>
          <p className="text-sm text-gray-400">预测记录与准确率统计</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">🕸️ 知识图谱</h3>
          <p className="text-sm text-gray-400">实体关系可视化</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
        <div className="p-6 bg-[#1f2937] rounded-lg border border-gray-800">
          <h3 className="text-lg font-semibold text-white mb-2">👤 用户画像</h3>
          <p className="text-sm text-gray-400">交易风格与偏好分析</p>
          <p className="text-xs text-gray-500 mt-4">即将上线...</p>
        </div>
      </div>
    </div>
  );
}
