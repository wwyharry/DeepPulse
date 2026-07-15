export default function Home() {
  return (
    <div className="space-y-8">
      {/* Hero */}
      <section className="text-center py-12">
        <h1 className="text-4xl font-bold text-white mb-4">
          DeepPulse
        </h1>
        <p className="text-xl text-gray-400 mb-8">
          AI 驱动的 A 股短线分析平台
        </p>
        <div className="flex justify-center gap-4">
          <a
            href="/chat"
            className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg font-medium transition"
          >
            开始分析
          </a>
          <a
            href="/market"
            className="px-6 py-3 bg-gray-700 hover:bg-gray-600 text-white rounded-lg font-medium transition"
          >
            查看行情
          </a>
        </div>
      </section>

      {/* 功能卡片 */}
      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        <FeatureCard
          title="AI 对话分析"
          description="流式输出，支持推理过程展示、工具调用可视化、图表内嵌"
          href="/chat"
          icon="🤖"
        />
        <FeatureCard
          title="实时行情"
          description="大盘/板块/个股实时数据，技术指标，资金流向"
          href="/market"
          icon="📈"
        />
        <FeatureCard
          title="条件选股"
          description="多维度技术条件筛选，形态识别，策略回测"
          href="/analysis/screener"
          icon="🔍"
        />
        <FeatureCard
          title="投资组合"
          description="自选股管理，交易记录，持仓分析，收益曲线"
          href="/portfolio"
          icon="💼"
        />
        <FeatureCard
          title="记忆系统"
          description="长期记忆，预测追踪，知识图谱，用户画像"
          href="/memory"
          icon="🧠"
        />
        <FeatureCard
          title="战法库"
          description="40 个内置短线战法，搜索浏览，AI 自动匹配"
          href="/strategies"
          icon="📚"
        />
      </section>
    </div>
  );
}

function FeatureCard({
  title,
  description,
  href,
  icon,
}: {
  title: string;
  description: string;
  href: string;
  icon: string;
}) {
  return (
    <a
      href={href}
      className="block p-6 bg-[#1f2937] hover:bg-[#374151] rounded-lg border border-gray-800 hover:border-gray-700 transition"
    >
      <div className="text-3xl mb-3">{icon}</div>
      <h3 className="text-lg font-semibold text-white mb-2">{title}</h3>
      <p className="text-sm text-gray-400">{description}</p>
    </a>
  );
}
