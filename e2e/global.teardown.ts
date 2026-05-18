import { deleteAllE2EEntities } from './support/api-seed';

export default async function globalTeardown(): Promise<void> {
  try {
    await deleteAllE2EEntities();
  } catch {
    // teardown best-effort — backend may already be stopped
  }
}
