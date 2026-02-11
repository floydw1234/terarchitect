/**
 * API Utility for Terarchitect Frontend
 */
export const API_URL = 'http://localhost:5010';

export interface Project {
  id: string;
  name: string;
  description?: string;
  project_path?: string;
  github_url?: string;
  created_at?: string;
  updated_at?: string;
}

export interface KanbanColumn {
  id: string;
  title: string;
  order: number;
}

export interface Ticket {
  id: string;
  project_id: string;
  column_id: string;
  title: string;
  description?: string;
  associated_node_ids?: string[];
  associated_edge_ids?: string[];
  priority: string;
  status: string;
  created_at?: string;
  updated_at?: string;
}

async function checkResponse<T = unknown>(response: Response): Promise<T> {
  if (!response.ok) {
    let msg = response.statusText;
    try {
      const body = await response.json();
      msg = (body && (body.error || body.message)) || msg;
    } catch {
      // ignore
    }
    throw new Error(`API ${response.status}: ${msg}`);
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

export async function getProjects(): Promise<Project[]> {
  const response = await fetch(`${API_URL}/api/projects`);
  return checkResponse<Project[]>(response);
}

export async function createProject(data: {
  name: string;
  description?: string;
  project_path?: string;
  github_url?: string;
}): Promise<Project> {
  const response = await fetch(`${API_URL}/api/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<Project>(response);
}

export async function getProject(projectId: string): Promise<Project> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}`);
  return checkResponse<Project>(response);
}

export async function updateProject(projectId: string, data: {
  name?: string;
  description?: string;
  project_path?: string;
  github_url?: string;
}): Promise<Project> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<Project>(response);
}

export async function deleteProject(projectId: string) {
  const response = await fetch(`${API_URL}/api/projects/${projectId}`, {
    method: 'DELETE',
  });
  return checkResponse(response);
}

export interface GraphResponse {
  id?: string;
  project_id?: string;
  nodes?: unknown[];
  edges?: unknown[];
  version?: number;
}

export async function getGraph(projectId: string): Promise<GraphResponse> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/graph`);
  return checkResponse<GraphResponse>(response);
}

export async function updateGraph(projectId: string, data: { nodes: any[]; edges: any[] }) {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/graph`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse(response);
}

export interface KanbanResponse {
  id?: string;
  project_id?: string;
  columns?: KanbanColumn[];
}

export async function getKanban(projectId: string): Promise<KanbanResponse> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/kanban`);
  return checkResponse<KanbanResponse>(response);
}

export async function updateKanban(projectId: string, data: { columns: any[] }) {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/kanban`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse(response);
}

export async function getTickets(projectId: string): Promise<Ticket[]> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets`);
  return checkResponse<Ticket[]>(response);
}

export async function createTicket(projectId: string, data: {
  column_id: string;
  title: string;
  description?: string;
  associated_node_ids?: string[];
  associated_edge_ids?: string[];
  priority?: string;
  status?: string;
}): Promise<Ticket> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<Ticket>(response);
}

export async function updateTicket(projectId: string, ticketId: string, data: {
  column_id?: string;
  title?: string;
  description?: string;
  priority?: string;
  status?: string;
  associated_node_ids?: string[];
  associated_edge_ids?: string[];
}): Promise<Ticket> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<Ticket>(response);
}

export async function deleteTicket(projectId: string, ticketId: string) {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}`, {
    method: 'DELETE',
  });
  return checkResponse(response);
}

export interface Note {
  id: string;
  project_id: string;
  node_id?: string;
  edge_id?: string;
  title?: string;
  content?: string;
  created_at?: string;
}

export async function getNotes(projectId: string): Promise<Note[]> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/notes`);
  return checkResponse<Note[]>(response);
}

export async function createNote(projectId: string, data: {
  title: string;
  content: string;
  node_id?: string;
  edge_id?: string;
}): Promise<Note> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/notes`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<Note>(response);
}

export async function updateNote(projectId: string, noteId: string, data: {
  title?: string;
  content?: string;
  node_id?: string;
  edge_id?: string;
}): Promise<Note> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/notes/${noteId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<Note>(response);
}

export async function deleteNote(projectId: string, noteId: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/notes/${noteId}`, {
    method: 'DELETE',
  });
  await checkResponse(response);
}
