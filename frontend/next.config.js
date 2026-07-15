/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 代理 API 请求到 FastAPI 后端
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/api/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
