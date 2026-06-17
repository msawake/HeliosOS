import React, { useState } from 'react'

export default function Settings() {
  const [apiKey, setApiKey] = useState(localStorage.getItem('forgeos_api_key') || '')
  const [saved, setSaved] = useState(false)

  const handleSave = () => {
    if (apiKey) {
      localStorage.setItem('forgeos_api_key', apiKey)
    } else {
      localStorage.removeItem('forgeos_api_key')
    }
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="page">
      <h2 className="page-title">Settings</h2>

      <div className="card" style={{ maxWidth: 600 }}>
        <h3>Authentication</h3>
        <div className="form-group">
          <label>API Key</label>
          <input
            type="password"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            placeholder="fos_..."
          />
          <small className="muted">Your Helios OS API key for accessing the platform</small>
        </div>
        <button className="btn btn-primary" onClick={handleSave}>
          {saved ? 'Saved!' : 'Save'}
        </button>
      </div>

      <div className="card" style={{ maxWidth: 600, marginTop: 20 }}>
        <h3>Company Info</h3>
        <div className="detail-grid">
          <div><strong>Platform:</strong> Helios OS v2.0.0</div>
          <div><strong>Company:</strong> LeadForge AI</div>
          <div><strong>Plan:</strong> Starter</div>
          <div><strong>Agents:</strong> 26</div>
        </div>
      </div>
    </div>
  )
}
