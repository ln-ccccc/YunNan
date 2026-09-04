import axios from 'axios';

export async function fetchSession() {
  const response = await axios.get('/api/auth/session');
  return response.data?.data || { authenticated: false, username: null };
}

export async function login(username, password) {
  const response = await axios.post('/api/auth/login', { username, password });
  return response.data?.data || { authenticated: false, username: null };
}

export async function logout() {
  await axios.post('/api/auth/logout');
}
