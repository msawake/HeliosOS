/** @type {import('next').NextConfig} */
const skipRewrite = process.env.SKIP_API_REWRITE === '1';

// Match bootstrap: PORT / --port (default 5000). Example: FORGEOS_API_URL=http://localhost:5001
const forgeosApiBase = (process.env.FORGEOS_API_URL || 'http://localhost:5000').replace(
  /\/$/,
  '',
);

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
        destination: `${forgeosApiBase}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
