import React, { useState, useEffect } from 'react'
import { getWorkflows, getWorkflow } from '../api'

function TaskNode({ task }) {
  const statusColors = {
    completed: '#00c853',
    in_progress: '#4a9eff',
    pending: '#8899a6',
    blocked: '#8899a6',
    failed: '#ff1744',
  }

  return (
    <div className="task-node" style={{ borderLeftColor: statusColors[task.status] || '#8899a6' }}>
      <div className="task-name">{task.name}</div>
      <div className="task-meta">
        <span className={`badge badge-${task.status}`}>{task.status}</span>
        <span>{task.assigned_agent}</span>
      </div>
    </div>
  )
}

export default function Workflows() {
  const [workflows, setWorkflows] = useState([])
  const [detail, setDetail] = useState(null)

  useEffect(() => {
    getWorkflows().then(setWorkflows).catch(() => {})
    const interval = setInterval(() => {
      getWorkflows().then(setWorkflows).catch(() => {})
    }, 10000)
    return () => clearInterval(interval)
  }, [])

  const selectWorkflow = async (wf) => {
    const d = await getWorkflow(wf.id)
    setDetail(d)
  }

  return (
    <div className="page">
      <h2 className="page-title">Workflows</h2>

      <div className="split-view">
        <div className="list-panel">
          {workflows.map(wf => {
            const p = wf.progress || {}
            const pct = p.total > 0 ? Math.round((p.completed / p.total) * 100) : 0
            return (
              <div key={wf.id} className="list-item" onClick={() => selectWorkflow(wf)}>
                <div className="list-item-title">{wf.name}</div>
                <div className="progress-bar small">
                  <div className="progress-fill" style={{ width: `${pct}%` }} />
                </div>
                <div className="list-item-meta">{pct}% complete</div>
              </div>
            )
          })}
          {workflows.length === 0 && <p className="muted">No active workflows</p>}
        </div>

        <div className="detail-panel">
          {detail ? (
            <div>
              <h3>{detail.workflow_name || 'Workflow'}</h3>
              <div className="task-list">
                {(detail.tasks || []).map(t => (
                  <TaskNode key={t.task_id || t.name} task={t} />
                ))}
              </div>
            </div>
          ) : (
            <p className="muted">Select a workflow to view tasks</p>
          )}
        </div>
      </div>
    </div>
  )
}
