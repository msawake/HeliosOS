import React, { useState, useEffect } from 'react'
import { getHealth } from '../api'

const DEPARTMENTS = ['executive', 'sales', 'marketing', 'finance', 'hr', 'legal', 'operations']

const TIER_LABELS = { 1: 'Executive', 2: 'Department Lead', 3: 'Worker' }
const TIER_COLORS = { 1: '#ff9100', 2: '#4a9eff', 3: '#8899a6' }

// Static agent registry (matches LeadForge config)
const AGENTS = [
  { id: 'exec-ceo', name: 'CEO', dept: 'executive', tier: 1, model: 'opus' },
  { id: 'exec-coo', name: 'COO', dept: 'executive', tier: 1, model: 'opus' },
  { id: 'exec-cfo', name: 'CFO', dept: 'executive', tier: 1, model: 'opus' },
  { id: 'sales-lead', name: 'Sales Lead', dept: 'sales', tier: 2, model: 'opus' },
  { id: 'sales-sdr', name: 'SDR', dept: 'sales', tier: 3, model: 'sonnet' },
  { id: 'sales-ae', name: 'Account Exec', dept: 'sales', tier: 3, model: 'opus' },
  { id: 'sales-ops', name: 'Pipeline Ops', dept: 'sales', tier: 3, model: 'sonnet' },
  { id: 'sales-researcher', name: 'Researcher', dept: 'sales', tier: 3, model: 'sonnet' },
  { id: 'sales-scorer', name: 'Lead Scorer', dept: 'sales', tier: 3, model: 'sonnet' },
  { id: 'sales-nurture', name: 'Nurture', dept: 'sales', tier: 3, model: 'sonnet' },
  { id: 'mkt-lead', name: 'Marketing Lead', dept: 'marketing', tier: 2, model: 'opus' },
  { id: 'mkt-content', name: 'Content', dept: 'marketing', tier: 3, model: 'sonnet' },
  { id: 'mkt-seo', name: 'SEO', dept: 'marketing', tier: 3, model: 'sonnet' },
  { id: 'mkt-email', name: 'Email', dept: 'marketing', tier: 3, model: 'sonnet' },
  { id: 'mkt-analytics', name: 'Analytics', dept: 'marketing', tier: 3, model: 'sonnet' },
  { id: 'mkt-demandgen', name: 'Demand Gen', dept: 'marketing', tier: 3, model: 'sonnet' },
  { id: 'mkt-ppc', name: 'PPC/Ads', dept: 'marketing', tier: 3, model: 'sonnet' },
  { id: 'fin-lead', name: 'Finance Lead', dept: 'finance', tier: 2, model: 'opus' },
  { id: 'fin-ar', name: 'Billing', dept: 'finance', tier: 3, model: 'sonnet' },
  { id: 'hr-lead', name: 'HR Lead', dept: 'hr', tier: 2, model: 'opus' },
  { id: 'legal-lead', name: 'Legal Lead', dept: 'legal', tier: 2, model: 'opus' },
  { id: 'legal-compliance', name: 'Compliance', dept: 'legal', tier: 3, model: 'opus' },
  { id: 'ops-lead', name: 'Ops Lead', dept: 'operations', tier: 2, model: 'opus' },
  { id: 'ops-vendor', name: 'Vendor Mgmt', dept: 'operations', tier: 3, model: 'sonnet' },
  { id: 'ops-monitoring', name: 'Monitoring', dept: 'operations', tier: 3, model: 'haiku' },
  { id: 'client-success', name: 'Client Success', dept: 'operations', tier: 3, model: 'sonnet' },
]

export default function Agents() {
  const [filter, setFilter] = useState('')
  const [health, setHealth] = useState(null)

  useEffect(() => {
    getHealth().then(setHealth).catch(() => {})
  }, [])

  const filtered = filter
    ? AGENTS.filter(a => a.dept === filter)
    : AGENTS

  return (
    <div className="page">
      <h2 className="page-title">Agents ({AGENTS.length})</h2>

      <div className="filters">
        <select value={filter} onChange={e => setFilter(e.target.value)}>
          <option value="">All Departments</option>
          {DEPARTMENTS.map(d => (
            <option key={d} value={d}>{d.charAt(0).toUpperCase() + d.slice(1)}</option>
          ))}
        </select>
      </div>

      <div className="agent-grid">
        {filtered.map(agent => (
          <div key={agent.id} className="agent-card">
            <div className="agent-tier" style={{ background: TIER_COLORS[agent.tier] }}>
              {TIER_LABELS[agent.tier]}
            </div>
            <div className="agent-name">{agent.name}</div>
            <div className="agent-meta">
              <span>{agent.id}</span>
              <span className="model-badge">{agent.model}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
