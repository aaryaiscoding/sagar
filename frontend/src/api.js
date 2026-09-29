import axios from 'axios'

const BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export const api = axios.create({ baseURL: BASE, timeout: 30000 })

export const runDetection = (payload) => api.post('/detect', payload).then(r => r.data)
export const uploadImage = (formData) =>
  api.post('/detect_image', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
     .then(r => r.data)
export const listSpills = () => api.get('/spills').then(r => r.data)
export const listVessels = () => api.get('/vessels').then(r => r.data)