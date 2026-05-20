import type { Metadata } from 'next';
import './globals.css';
import { AppShell } from '@/components/AppShell';
import { AuthProvider } from '@/lib/auth';

export const metadata: Metadata = {
  title: 'ForgeOS Platform',
  description: 'Multi-stack AI agent platform dashboard',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <AppShell>{children}</AppShell>
        </AuthProvider>
      </body>
    </html>
  );
}
