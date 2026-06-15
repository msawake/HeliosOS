// Let `tsc --noEmit` resolve CSS side-effect imports (Next.js handles the
// actual bundling; this just keeps a standalone typecheck clean).
declare module '*.css';
