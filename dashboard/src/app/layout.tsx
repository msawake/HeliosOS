import type { Metadata } from 'next';
import './globals.css';
import { fontVariables } from '@/lib/fonts';
import { AppShell } from '@/components/layout/AppShell';
import { AuthProvider } from '@/lib/auth';

export const metadata: Metadata = {
  title: 'Helios OS',
  description: 'The agentic harness — deploy, invoke, and govern agents.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={fontVariables}>
      <body>
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
