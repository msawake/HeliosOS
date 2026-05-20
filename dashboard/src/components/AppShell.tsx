'use client';

import { usePathname } from 'next/navigation';
import { Sidebar } from '@/components/Sidebar';
import { RequireAuth } from '@/lib/auth';

/**
 * Top-level shell that decides between:
 *  - /login → render children full-bleed (no sidebar, no auth gate)
 *  - everything else → RequireAuth wrapper + sidebar + main panel
 *
 * This avoids the loop where a logged-out user sees the sidebar flash
 * before the redirect.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isLogin = pathname === '/login';

  if (isLogin) {
    return <>{children}</>;
  }

  return (
    <RequireAuth>
      <div className="flex h-screen">
        <Sidebar />
        <main className="flex-1 overflow-auto">
          <div className="max-w-7xl mx-auto px-6 py-8">{children}</div>
        </main>
      </div>
    </RequireAuth>
  );
}
