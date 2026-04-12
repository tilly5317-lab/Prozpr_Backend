import type {
  ChatRequest,
  ChatResponse,
  SaveClientResponse,
  ClientDetailResponse,
  ClientListResponse
} from './types';

const API_BASE = import.meta.env.VITE_API_URL ?? '/api';

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {})
    },
    ...init
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function sendChat(payload: ChatRequest): Promise<ChatResponse> {
  return apiRequest<ChatResponse>('/chat', {
    method: 'POST',
    body: JSON.stringify(payload)
  });
}

export async function saveClientFromSession(sessionId: string): Promise<SaveClientResponse> {
  return apiRequest<SaveClientResponse>(`/clients/from-session/${sessionId}`, {
    method: 'POST'
  });
}

export async function getClientDetail(clientId: number): Promise<ClientDetailResponse> {
  return apiRequest<ClientDetailResponse>(`/clients/${clientId}`);
}
export async function uploadStatement(sessionId: string, file: File): Promise<{ session_id: string; message: string; }> {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch(`${API_BASE}/clients/${sessionId}/upload-statement`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Request failed with status ${response.status}`);
  }
  return response.json();
}

export async function listClients(): Promise<ClientListResponse> {
  return apiRequest<ClientListResponse>('/clients');
}
