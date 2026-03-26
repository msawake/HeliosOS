import React, { useState, useEffect } from 'react'
import { getOverview } from '../api'

function KPICard({ label, value, color = 'blue' }) {
  return (
    <div className="kpi-card">
      <div className={`kpi-value ${color}`}>{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  )
}

function ApprovalPreview({ approval }) {
  return (
    <div className={`approval-card ${approval.risk || 'low'}`}>
      <div className="approval-title">{approval.title}</div>
      <div className="approval-meta">
        <span>{approval.category}</span>
        <span>{approval.agent}</span>
        <span>SLA: {approval.sla_hours}h</span>
      </div>
    </div>
  )
}

function WorkflowPreview({ workflow }) {
  const progress = workflow.progress || {}
  const pct = progress.total > 0
    ? Math.round((progress.completed / progress.total) * 100)
    : 0

  return (
    <div className="workflow-card">
      <div className="workflow-name">{workflow.name}</div>
      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="workflow-meta">{progress.completed}/{progress.total} tasks</div>
    </div>
  )
}

export default function Dashboard() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getOverview()
      .then(setData)
      .catch(e => setError(e.message))

    const interval = setInterval(() => {
      getOverview().then(setData).catch(() => {})
    }, 30000)
    return () => clearInterval(interval)
  }, [])

  if (error) return <div className="error-banner">{error}</div>
  if (!data) return <div className="loading">Loading dashboard...</div>

  const kpis = data.kpis || {}
  const approvals = data.pending_approvals || []
  const workflows = data.active_workflows || []

  return (
    <div className="page">
      <h2 className="page-title">Dashboard</h2>

      <div className="kpi-grid">
        <KPICard label="Pending Approvals" value={approvals.length} color={approvals.length > 0 ? 'red' : 'green'} />
        <KPICard label="Active Workflows" value={workflows.length} color="blue" />
        {Object.entries(kpis).slice(0, 4).map(([key, val]) => (
          <KPICard key={key} label={key.replace(/_/g, ' ')} value={typeof val === 'number' ? val.toFixed(0) : val} />
        ))}
      </div>

      <div className="two-column">
        <div className="card">
          <h3>Pending Approvals</h3>
          {approvals.length === 0
            ? <p className="muted">No pending approvals</p>
            : approvals.map(a => <ApprovalPreview key={a.id} approval={a} />)
          }
        </div>

        <div className="card">
          <h3>Active Workflows</h3>
          {workflows.length === 0
            ? <p className="muted">No active workflows</p>
            : workflows.map(w => <WorkflowPreview key={w.id} workflow={w} />)
          }
        </div>
      </div>
    </div>
  )
}
