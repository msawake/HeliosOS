/** @type {import('next').NextConfig} */
const skipRewrite = process.env.SKIP_API_REWRITE === '1';

// Match bootstrap: PORT / --port (default 5000). Example: FORGEOS_API_URL=http://localhost:5001
const forgeosApiBase = (process.env.FORGEOS_API_URL || 'http://localhost:5000').replace(
  /\/$/,
  '',
);

const nextConfig = {
  output: 'standalone',

  // Increase proxy timeout for long-running wizard/admin requests (Opus tool loops)
  experimental: {
    proxyTimeout: 300000, // 5 minutes
    // Rewrite barrel imports to per-icon paths so a single `import { X }`
    // doesn't drag the whole icon set through the compiler (huge dev-compile win).
    optimizePackageImports: ['@phosphor-icons/react'],
  },

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
