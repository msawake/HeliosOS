'use client';

import { useState } from 'react';

/** Copy-to-clipboard with a transient confirmation flag; shared by the code
 * blocks and key reveals so the state machine lives once. */
export function useCopy(timeoutMs = 1600) {
  const [copied, setCopied] = useState(false);
  const copy = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), timeoutMs);
    } catch {
      // Clipboard unavailable; selection still works.
    }
  };
  return { copied, copy };
}
