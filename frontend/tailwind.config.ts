import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // 金融暗色主题
        up: '#ef4444',      // 涨 - 红色
        down: '#22c55e',    // 跌 - 绿色
        flat: '#6b7280',    // 平 - 灰色
      },
    },
  },
  plugins: [],
};

export default config;
