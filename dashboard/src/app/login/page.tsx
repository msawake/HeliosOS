'use client';

import Link from 'next/link';

export default function LoginPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-950">
      <div className="w-full max-w-sm p-8 bg-gray-900 border border-gray-800 rounded-xl text-center">
        <h1 className="text-2xl font-bold text-white mb-2">ForgeOS</h1>
        <p className="text-gray-400 text-sm mb-6">
          Authentication is not required in local development mode.
        </p>
        <Link href="/"
          className="inline-block px-6 py-2 bg-sky-600 hover:bg-sky-500 text-white font-medium rounded-lg transition-colors">
          Go to Dashboard
        </Link>
      </div>
    </div>
  );
}
