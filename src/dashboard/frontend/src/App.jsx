import React from 'react'
import { Routes, Route, Link, useLocation } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Approvals from './pages/Approvals'
import Workflows from './pages/Workflows'
import Agents from './pages/Agents'
import Settings from './pages/Settings'

const NAV_ITEMS = [
  { path: '/', label: 'Dashboard', icon: '◉' },
  { path: '/approvals', label: 'Approvals', icon: '✓' },
  { path: '/workflows', label: 'Workflows', icon: '⟳' },
  { path: '/agents', label: 'Agents', icon: '⬡' },
  { path: '/settings', label: 'Settings', icon: '⚙' },
]

function Sidebar() {
  const location = useLocation()

  return (
    <nav className="sidebar">
      <div className="sidebar-header">
        <h1 className="logo">ForgeOS</h1>
        <span className="logo-sub">Command Center</span>
      </div>
      <ul className="nav-list">
        {NAV_ITEMS.map(item => (
          <li key={item.path}>
            <Link
              to={item.path}
              className={`nav-link ${location.pathname === item.path ? 'active' : ''}`}
            >
              <span className="nav-icon">{item.icon}</span>
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
      <div className="sidebar-footer">
        <div className="status-dot green" />
        <span>System Online</span>
      </div>
    </nav>
  )
}

export default function App() {
  return (
    <div className="app-layout">
      <Sidebar />
      <main className="main-content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/workflows" element={<Workflows />} />
          <Route path="/agents" element={<Agents />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  )
}
