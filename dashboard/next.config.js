/** @type {import('next').NextConfig} */
const skipRewrite = process.env.SKIP_API_REWRITE === '1';

const nextConfig = {
  output: 'standalone',

  async rewrites() {
    // In Kubernetes, route /api at Ingress to the API Service; no dev proxy needed in-container.
    if (skipRewrite) {
      return [];
    }
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:5000/api/:path*',
      },
    ];
  },
};

module.exports = nextConfig;
