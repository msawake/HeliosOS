'use client';

import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { Badge } from '@/components/Badge';

interface Task {
  task_id?: string;
  id?: string;
  name?: string;
  status?: string;
  started_at?: string;
  completed_at?: string;
  duration_seconds?: number;
  assigned_to?: string;
  error?: string;
  [key: string]: unknown;
}

interface WorkflowReport {
  id?: string;
  workflow_id?: string;
  name?: string;
  type?: string;
  status?: string;
  priority?: string;
  created_at?: string;
  progress?: { total: number; completed: number; percentage?: number };
  tasks?: Task[];
  error?: string;
  [key: string]: unknown;
}

export default function WorkflowDetailPage() {
  const params = useParams();
  const workflowId = params.id as string;
  const [report, setReport] = useState<WorkflowReport | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/workflows/${encodeURIComponent(workflowId)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then(setReport)
      .catch(() => setReport(null))
      .finally(() => setLoading(false));
  }, [workflowId]);

  if (loading) return <p className="text-gray-400">Loading workflow…</p>;

  if (!report || report.error) {
    return (
      <div>
        <Link href="/workflows" className="text-sm text-gray-500 hover:text-white mb-4 inline-block">
          ← Back to workflows
        </Link>
        <p className="text-gray-500">
          {report?.error || 'Workflow not found.'}
        </p>
      </div>
    );
  }

  const progress = report.progress || { total: 0, completed: 0 };
  const percentage = progress.percentage ?? (
    progress.total > 0 ? Math.round((progress.completed / progress.total) * 100) : 0
  );
  const tasks = report.tasks || [];

  return (
    <div>
      <Link href="/workflows" className="text-sm text-gray-500 hover:text-white mb-4 inline-block">
        ← Back to workflows
      </Link>

      <div className="flex items-center gap-3 mb-2">
        <h1 className="text-2xl font-semibold text-[#0d0d0d]">{report.name || 'Workflow'}</h1>
        {report.status && <Badge label={report.status} variant={report.status} />}
        {report.priority && <Badge label={report.priority} variant="scheduled" />}
      </div>
      <p className="text-xs text-gray-500 font-mono mb-6">{report.workflow_id || report.id || workflowId}</p>

      <div className="bg-white border border-[#e5e5e5] rounded-xl p-5 mb-6">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Progress</h2>
        <div className="flex items-center gap-4 mb-3">
          <div className="flex-1 bg-[#f7f7f8] rounded-full h-3">
            <div
              className="bg-[#10A37F] h-3 rounded-full transition-all"
              style={{ width: `${percentage}%` }}
            />
          </div>
          <span className="text-[#0d0d0d] font-bold text-lg w-16 text-right">{percentage}%</span>
        </div>
        <p className="text-sm text-gray-500">
          {progress.completed} of {progress.total} task(s) completed
        </p>
      </div>

      <div className="bg-white border border-[#e5e5e5] rounded-xl p-5">
        <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wide mb-3">Tasks</h2>
        {tasks.length === 0 ? (
          <p className="text-gray-500 text-sm">No task data available.</p>
        ) : (
          <div className="space-y-2">
            {tasks.map((t, i) => {
              const id = t.task_id || t.id || `task-${i}`;
              return (
                <div key={id} className="bg-[#f7f7f8] border border-[#e5e5e5] rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-[#0d0d0d] font-medium text-sm">{t.name || id}</span>
                    {t.status && <Badge label={t.status} variant={t.status} />}
                  </div>
                  {t.assigned_to && (
                    <p className="text-xs text-gray-500">Assigned to: <span className="text-[#6e6e80]">{t.assigned_to}</span></p>
                  )}
                  {t.error && (
                    <p className="text-xs text-red-400 mt-1 font-mono">{t.error}</p>
                  )}
                  {(t.started_at || t.completed_at) && (
                    <p className="text-xs text-gray-600 mt-1">
                      {t.started_at && <>Started: {new Date(t.started_at).toLocaleString()}</>}
                      {t.completed_at && <> • Done: {new Date(t.completed_at).toLocaleString()}</>}
                      {typeof t.duration_seconds === 'number' && <> • {t.duration_seconds.toFixed(1)}s</>}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
