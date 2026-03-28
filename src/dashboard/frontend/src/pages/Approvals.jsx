import React, { useState, useEffect } from 'react'
import { getApprovals, getApproval, approveRequest, rejectRequest } from '../api'

function ApprovalDetail({ approval, onDecision }) {
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)

  const handleApprove = async () => {
    setLoading(true)
    await approveRequest(approval.id, reason)
    onDecision()
  }

  const handleReject = async () => {
    setLoading(true)
    await rejectRequest(approval.id, reason)
    onDecision()
  }

  const hoursRemaining = approval.deadline
    ? Math.max(0, (new Date(approval.deadline) - new Date()) / 3600000).toFixed(1)
    : '?'

  return (
    <div className="approval-detail">
      <h3>{approval.title}</h3>
      <div className="detail-grid">
        <div><strong>Category:</strong> {approval.category}</div>
        <div><strong>Agent:</strong> {approval.agent}</div>
        <div><strong>Department:</strong> {approval.department}</div>
        <div><strong>Risk:</strong> <span className={`badge ${approval.risk}`}>{approval.risk}</span></div>
        <div><strong>SLA:</strong> {approval.sla_hours}h ({hoursRemaining}h remaining)</div>
      </div>
      <p className="description">{approval.description}</p>

      <div className="decision-form">
        <textarea
          placeholder="Reason (optional)"
          value={reason}
          onChange={e => setReason(e.target.value)}
          rows={2}
        />
        <div className="btn-group">
          <button className="btn btn-approve" onClick={handleApprove} disabled={loading}>
            Approve
          </button>
          <button className="btn btn-reject" onClick={handleReject} disabled={loading}>
            Reject
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Approvals() {
  const [approvals, setApprovals] = useState([])
  const [selected, setSelected] = useState(null)
  const [category, setCategory] = useState('')

  const loadApprovals = () => {
    getApprovals(category).then(setApprovals).catch(() => {})
  }

  useEffect(() => { loadApprovals() }, [category])

  const handleDecision = () => {
    setSelected(null)
    loadApprovals()
  }

  return (
    <div className="page">
      <h2 className="page-title">Approvals</h2>

      <div className="filters">
        <select value={category} onChange={e => setCategory(e.target.value)}>
          <option value="">All Categories</option>
          <option value="financial">Financial</option>
          <option value="content">Content</option>
          <option value="contract">Contract</option>
          <option value="ad_spend">Ad Spend</option>
          <option value="tool_authorization">Tool Auth</option>
        </select>
        <span className="count">{approvals.length} pending</span>
      </div>

      <div className="split-view">
        <div className="list-panel">
          {approvals.map(a => (
            <div
              key={a.id}
              className={`list-item ${selected?.id === a.id ? 'selected' : ''} ${a.risk || 'low'}`}
              onClick={() => setSelected(a)}
            >
              <div className="list-item-title">{a.title}</div>
              <div className="list-item-meta">
                {a.category} | {a.agent} | {a.sla_hours}h SLA
              </div>
            </div>
          ))}
          {approvals.length === 0 && <p className="muted">No pending approvals</p>}
        </div>

        <div className="detail-panel">
          {selected
            ? <ApprovalDetail approval={selected} onDecision={handleDecision} />
            : <p className="muted">Select an approval to review</p>
          }
        </div>
      </div>
    </div>
  )
}
