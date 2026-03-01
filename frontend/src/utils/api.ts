/**
 * API Utility for Terarchitect Frontend
 */
export const API_URL = 'http://localhost:5010';

export type ProjectExecutionMode = 'docker' | 'local';

export interface Project {
  id: string;
  name: string;
  description?: string;
  github_url?: string;
  /** When execution_mode is "local", agent runs on host at this path. */
  project_path?: string | null;
  execution_mode?: ProjectExecutionMode;
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
  pr_url?: string | null;
  pr_number?: number | null;
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
  github_url?: string;
  execution_mode?: ProjectExecutionMode;
  project_path?: string;
  /** If true, project is from an existing repo; default "Project setup" ticket is not created. */
  is_existing_repo?: boolean;
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
  github_url?: string;
  execution_mode?: ProjectExecutionMode;
  project_path?: string | null;
}): Promise<Project> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<Project>(response);
}

export async function deleteProject(projectId: string, confirmName: string) {
  const response = await fetch(`${API_URL}/api/projects/${projectId}`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm_name: confirmName }),
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
  return checkResponse<{ version: number }>(response);
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

export interface ExecutionLogEntry {
  id: string;
  step: string;
  summary: string;
  raw_output?: string;
  success: boolean;
  created_at?: string;
}

export async function getTicketLogs(projectId: string, ticketId: string): Promise<ExecutionLogEntry[]> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}/logs`);
  return checkResponse<ExecutionLogEntry[]>(response);
}

export async function cancelTicketExecution(projectId: string, ticketId: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}/cancel`, {
    method: 'POST',
  });
  await checkResponse(response);
}

export interface Note {
  id: string;
  project_id: string;
  node_ids: string[];
  edge_ids: string[];
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
  node_ids?: string[];
  edge_ids?: string[];
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
  node_ids?: string[];
  edge_ids?: string[];
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

export interface ReviewCommit {
  sha: string;
  message: string;
}

export interface ReviewTestFile {
  path: string;
  test_names: string[];
}

export interface ReviewComment {
  author: string;
  body: string;
  created_at: string | null;
}

export interface ReviewResponse {
  summary: string;
  commits: ReviewCommit[];
  test_files: ReviewTestFile[];
  tests_description?: string;
  comments: ReviewComment[];
  pr_url: string;
  pr_number: number;
  pr_state: string;
  merged: boolean;
}

export async function getReview(projectId: string, ticketId: string): Promise<ReviewResponse> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}/review`);
  return checkResponse<ReviewResponse>(response);
}

export interface ReviewListEntry {
  id: string;
  title: string;
  pr_url: string | null;
  pr_number: number | null;
  pr_state: string;
  merged: boolean;
}

export async function getReviewList(projectId: string): Promise<ReviewListEntry[]> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/review`);
  return checkResponse<ReviewListEntry[]>(response);
}

export async function postReviewComment(projectId: string, ticketId: string, body: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}/review/comment`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ body }),
  });
  await checkResponse(response);
}

export async function approveReview(projectId: string, ticketId: string, body?: string): Promise<void> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}/review/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body != null ? { body } : {}),
  });
  await checkResponse(response);
}

export async function mergeReview(projectId: string, ticketId: string, mergeMethod?: 'merge' | 'squash' | 'rebase'): Promise<void> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/tickets/${ticketId}/review/merge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(mergeMethod ? { merge_method: mergeMethod } : {}),
  });
  await checkResponse(response);
}

/** App settings: sensitive keys are boolean (is set), plain keys are string | null. */
export type AppSettingsResponse = Record<string, boolean | string | null>;

export async function getSettings(): Promise<AppSettingsResponse> {
  const response = await fetch(`${API_URL}/api/settings`);
  return checkResponse<AppSettingsResponse>(response);
}

export interface SettingIssue {
  key: string;
  label: string;
  reason: string;
}

export interface SettingsCheckResponse {
  ready: boolean;
  missing_required: SettingIssue[];
  warnings: SettingIssue[];
}

export async function getSettingsCheck(): Promise<SettingsCheckResponse> {
  const response = await fetch(`${API_URL}/api/settings/check`);
  return checkResponse<SettingsCheckResponse>(response);
}

/** Body: include only keys to update. Empty string = clear that key. */
export async function updateSettings(data: Record<string, string>): Promise<AppSettingsResponse> {
  const response = await fetch(`${API_URL}/api/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<AppSettingsResponse>(response);
}
