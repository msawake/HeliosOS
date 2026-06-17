/**
 * Helios OS API client.
 * Handles all communication with the Flask backend.
 */

const BASE = '/api'

async function request(path, options = {}) {
  const token = localStorage.getItem('forgeos_token')
  const apiKey = localStorage.getItem('forgeos_api_key')

  const headers = { 'Content-Type': 'application/json', ...options.headers }
  if (token) headers['Authorization'] = `Bearer ${token}`
  else if (apiKey) headers['X-API-Key'] = apiKey

  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }))
    throw new Error(err.error || `API error: ${res.status}`)
  }
  return res.json()
}

// Dashboard
export const getOverview = () => request('/overview')
export const getHealth = () => request('/health')
export const getUsage = () => request('/usage')

// Approvals
export const getApprovals = (category) =>
  request(`/approvals${category ? `?category=${category}` : ''}`)
export const getApproval = (id) => request(`/approvals/${id}`)
export const approveRequest = (id, reason = '') =>
  request(`/approvals/${id}/approve`, {
    method: 'POST',
    body: JSON.stringify({ approved_by: 'dashboard_user', reason }),
  })
export const rejectRequest = (id, reason = '') =>
  request(`/approvals/${id}/reject`, {
    method: 'POST',
    body: JSON.stringify({ rejected_by: 'dashboard_user', reason }),
  })

// Workflows
export const getWorkflows = () => request('/workflows')
export const getWorkflow = (id) => request(`/workflows/${id}`)

// Events
export const getEvents = (dept, status) => {
  const params = new URLSearchParams()
  if (dept) params.set('department', dept)
  if (status) params.set('status', status)
  return request(`/events?${params}`)
}

// Auth
export const getMe = () => request('/me')

// Tenants (admin)
export const createTenant = (data) =>
  request('/tenants', { method: 'POST', body: JSON.stringify(data) })
export const listTenants = () => request('/tenants')
