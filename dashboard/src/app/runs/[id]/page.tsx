'use client';

import { useParams } from 'next/navigation';
import { useRun } from '@/lib/hooks/useRun';
import { PageHeader } from '@/components/layout/PageHeader';
import { Card, CardContent } from '@/components/ui/card';
import { RunPanel } from '@/components/RunPanel';

export default function RunPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);
  const { run, polling, error } = useRun(id);

  return (
    <div>
      <PageHeader
        title="Run"
        description={<span className="font-mono text-xs">{id}</span>}
        back={{ href: '/', label: 'Agents' }}
      />
      <Card>
        <CardContent className="py-5">
          <RunPanel run={run} polling={polling} error={error} />
        </CardContent>
      </Card>
    </div>
  );
}
