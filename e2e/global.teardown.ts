import { deleteAllE2EEntities } from './support/api-seed';

export default async function globalTeardown(): Promise<void> {
  try {
    await deleteAllE2EEntities();
  } catch (err) {
    console.warn('[teardown] cleanup skipped:', err);
  }
}
