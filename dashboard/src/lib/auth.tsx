'use client';

import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react';
import { useRouter, usePathname } from 'next/navigation';

interface AuthUser {
  user_id: string;
  email: string;
  tenant_id: string;
  role: string;
  name: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  token: string | null;
  isAuthenticated: boolean;
  loaded: boolean;  // true once sessionStorage restore completes
  login: (token: string) => Promise<void>;
  loginWithApiKey: (apiKey: string) => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = 'forgeos_token';
const API_KEY_KEY = 'forgeos_api_key';

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const router = useRouter();

  // Restore from sessionStorage on mount
  useEffect(() => {
    const savedToken = sessionStorage.getItem(TOKEN_KEY);
    const savedKey = sessionStorage.getItem(API_KEY_KEY);
    if (savedToken) {
      setToken(savedToken);
      fetchMe(savedToken, null)
        .then(setUser)
        .catch(() => {
          sessionStorage.removeItem(TOKEN_KEY);
        })
        .finally(() => setLoaded(true));
    } else if (savedKey) {
      setApiKey(savedKey);
      fetchMe(null, savedKey)
        .then(setUser)
        .catch(() => {
          sessionStorage.removeItem(API_KEY_KEY);
        })
        .finally(() => setLoaded(true));
    } else {
      setLoaded(true);
    }
  }, []);

  const login = useCallback(async (jwt: string) => {
    setToken(jwt);
    sessionStorage.setItem(TOKEN_KEY, jwt);
    const me = await fetchMe(jwt, null);
    setUser(me);
    router.push('/');
  }, [router]);

  const loginWithApiKey = useCallback((key: string) => {
    setApiKey(key);
    sessionStorage.setItem(API_KEY_KEY, key);
    fetchMe(null, key).then((me) => {
      setUser(me);
      router.push('/');
    });
  }, [router]);

  const logout = useCallback(() => {
    setUser(null);
    setToken(null);
    setApiKey(null);
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(API_KEY_KEY);
    router.push('/login');
  }, [router]);

  const isAuthenticated = user !== null;

  return (
    <AuthContext.Provider value={{ user, token: token || apiKey, isAuthenticated, loaded, login, loginWithApiKey, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
}

/** Wrapper that redirects to /login if not authenticated.
 *
 * Honors `NEXT_PUBLIC_REQUIRE_AUTH` — when "0" or unset, auth is optional
 * and all pages render without gating. Set to "1" to enforce the login flow.
 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, loaded } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  const required = process.env.NEXT_PUBLIC_REQUIRE_AUTH === '1';

  useEffect(() => {
    if (!required) return;
    if (!loaded) return;  // Wait for sessionStorage restore
    if (!isAuthenticated && pathname !== '/login') {
      router.push('/login');
    }
  }, [required, loaded, isAuthenticated, pathname, router]);

  // When auth is not required, always render
  if (!required) {
    return <>{children}</>;
  }

  // Still loading — show nothing briefly instead of flashing redirect
  if (!loaded) {
    return <div className="min-h-screen bg-page" />;
  }

  if (!isAuthenticated && pathname !== '/login') {
    return null;
  }
  return <>{children}</>;
}

/** Get the auth header value for API requests. */
export function getAuthHeaders(): Record<string, string> {
  const token = sessionStorage.getItem(TOKEN_KEY);
  if (token) return { Authorization: `Bearer ${token}` };
  const key = sessionStorage.getItem(API_KEY_KEY);
  if (key) return { 'X-API-Key': key };
  return {};
}

async function fetchMe(token: string | null, apiKey: string | null): Promise<AuthUser> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  else if (apiKey) headers['X-API-Key'] = apiKey;

  const res = await fetch('/api/me', { headers });
  if (!res.ok) throw new Error('Not authenticated');
  return res.json();
}
