/**
 * API Utility for Terarchitect Frontend
 */

// Derive the backend URL from the browser's current hostname at runtime.
// This means the frontend works regardless of whether you access it locally,
// via LAN IP, SSH port-forward, or any other hostname — no rebuild required.
// REACT_APP_API_URL can still override this (e.g. for a separate backend host).
function resolveApiUrl(): string {
  if (process.env.REACT_APP_API_URL) {
    return process.env.REACT_APP_API_URL.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') {
    return `${window.location.protocol}//${window.location.hostname}:5010`;
  }
  return 'http://localhost:5010';
}

export const API_URL = resolveApiUrl();

// ---------------------------------------------------------------------------
// AgentHub channel naming (mirrors backend channel_service.py)
// ---------------------------------------------------------------------------

/** Returns the AgentHub channel name for a ticket (ticket-{uuid_no_dashes[:24]}). */
export function ticketChannelName(ticketId: string): string {
  const short = ticketId.replace(/-/g, '').slice(0, 24);
  return `ticket-${short}`;
}

function resolveAgenthubUrl(): string {
  if (process.env.REACT_APP_AGENTHUB_URL) {
    return process.env.REACT_APP_AGENTHUB_URL.replace(/\/$/, '');
  }
  if (typeof window !== 'undefined') {
    return `${window.location.protocol}//${window.location.hostname}:8088`;
  }
  return 'http://localhost:8088';
}

export const AGENTHUB_URL = resolveAgenthubUrl();

export type ProjectExecutionMode = 'docker' | 'local';
export type ProjectGitMode = 'swarm';

export interface Project {
  id: string;
  name: string;
  description?: string;
  github_url?: string;
  /** When execution_mode is "local", agent runs on host at this path. */
  project_path?: string | null;
  execution_mode?: ProjectExecutionMode;
  git_mode?: ProjectGitMode;
  /** Last shipped main commit hash. All new agent work builds on top of this. */
  shipped_frontier?: string | null;
  shipped_frontier_updated_at?: string | null;
  /** Set on project creation when frontier could not be auto-detected. */
  frontier_warning?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface KanbanColumn {
  id: string;
  title: string;
  order: number;
}

export interface LatestAttempt {
  id: string;
  short_commit_hash: string | null;
  status: string;
  wave_num: number;
  attempt_num: number;
  summary: string | null;
  test_status: string | null;
  stale: boolean | null;
}

export type IntentStatus = 'draft' | 'ready' | 'active' | 'blocked' | 'archived';
export type RiskLevel = 'low' | 'medium' | 'high';

/** Computed display state derived from intent + execution data. */
export type DisplayState =
  | 'draft' | 'blocked' | 'queued' | 'running'
  | 'attempt_ready' | 'accepted' | 'stale'
  | 'composed' | 'release_pr_open' | 'shipped'
  | 'failed' | 'archived';

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
  failed_count?: number;
  depends_on_ticket_ids?: string[];
  is_running?: boolean;
  created_at?: string;
  updated_at?: string;
  latest_attempt?: LatestAttempt | null;
  /** Most recent accepted (or better) attempt — may differ from latest_attempt if agent retried and failed. */
  accepted_attempt?: LatestAttempt | null;
  // Intent fields
  intent_status: IntentStatus;
  display_state: DisplayState;
  rationale?: string | null;
  acceptance_criteria?: string | null;
  constraints?: string | null;
  value_score?: number | null;
  risk_level?: RiskLevel | null;
  created_source?: string | null;
}

export interface TicketAttempt {
  id: string;
  project_id: string;
  ticket_id: string;
  agenthub_commit_hash: string;
  short_commit_hash: string | null;
  base_hash: string | null;
  wave_num: number;
  attempt_num: number;
  agent_id: string | null;
  status: string;
  summary: string | null;
  validation_error: string | null;
  test_status: string | null;
  test_output?: string | null;
  stale: boolean | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ShipRun {
  id: string;
  project_id: string;
  wave_num: number;
  status: string;
  error: string | null;
  release_branch: string | null;
  base_main_hash: string | null;
  composed_commit_hash: string | null;
  changed_files: string[];
  summary: string | null;
  test_status: string | null;
  test_output: string | null;
  release_pr_url: string | null;
  release_pr_number: number | null;
  shipped_at: string | null;
  shipped_commit_hash: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface WaveSummary {
  wave_num: number;
  ticket_count: number;
  accepted_count: number;
  all_done: boolean;
  ship_run: ShipRun | null;
}

export interface WaveDetail {
  wave_num: number;
  tickets: Ticket[];
  accepted_attempts: TicketAttempt[];
  ship_run: ShipRun | null;
  can_compose: boolean;
  all_done: boolean;
  shipped_frontier: string | null;
  stale_count: number;
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
  git_mode?: ProjectGitMode;
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
  git_mode?: ProjectGitMode;
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

export interface GenerateGraphResponse {
  nodes: unknown[];
  edges: unknown[];
  version: number;
  node_count: number;
  edge_count: number;
}

export async function generateGraph(projectId: string): Promise<GenerateGraphResponse> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 360_000); // 6 min
  try {
    const response = await fetch(`${API_URL}/api/projects/${projectId}/graph/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      signal: controller.signal,
    });
    return checkResponse<GenerateGraphResponse>(response);
  } finally {
    clearTimeout(timeoutId);
  }
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
  depends_on_ticket_ids?: string[];
  intent_status?: IntentStatus;
  rationale?: string;
  acceptance_criteria?: string;
  constraints?: string;
  value_score?: number;
  risk_level?: RiskLevel;
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
  depends_on_ticket_ids?: string[];
  intent_status?: IntentStatus;
  rationale?: string;
  acceptance_criteria?: string;
  constraints?: string;
  value_score?: number;
  risk_level?: RiskLevel;
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

// ---------------------------------------------------------------------------
// Ship Room API
// ---------------------------------------------------------------------------

export async function getShipWaves(projectId: string): Promise<WaveSummary[]> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/waves`);
  return checkResponse<WaveSummary[]>(response);
}

export async function getShipWaveDetail(projectId: string, waveNum: number): Promise<WaveDetail> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/waves/${waveNum}`);
  return checkResponse<WaveDetail>(response);
}

export async function composeWave(projectId: string, waveNum: number): Promise<ShipRun> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/waves/${waveNum}/compose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return checkResponse<ShipRun>(response);
}

export async function shipWave(projectId: string, waveNum: number): Promise<ShipRun> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/waves/${waveNum}/ship`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return checkResponse<ShipRun>(response);
}

export async function sendWaveFeedback(
  projectId: string,
  waveNum: number,
  message: string,
  targetTicketId?: string,
): Promise<void> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/waves/${waveNum}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, target_ticket_id: targetTicketId }),
  });
  await checkResponse(response);
}

export async function getTicketAttempts(
  projectId: string,
  ticketId: string,
): Promise<TicketAttempt[]> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/tickets/${ticketId}/attempts`,
  );
  return checkResponse<TicketAttempt[]>(response);
}

export async function acceptAttempt(
  projectId: string,
  ticketId: string,
  attemptId: string,
): Promise<TicketAttempt> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/tickets/${ticketId}/attempts/${attemptId}/accept`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  return checkResponse<TicketAttempt>(response);
}

/** An event posted to an AgentHub channel — execution ledger entry. */
export interface AgentHubEvent {
  id: number;
  channel_id: number;
  agent_id: string;
  parent_id: number | null;
  content: string;
  created_at: string;
  /** Channel the event was posted to */
  _channel: string;
  /** 'wave' = wave-level event, 'ticket' = per-ticket event */
  _channel_type: 'wave' | 'ticket';
  /** Title of the ticket this event belongs to (ticket events only) */
  _ticket_title?: string;
}

/** @deprecated Use AgentHubEvent */
export type TimelinePost = AgentHubEvent;

/** Current AgentHub root state for a project. */
export interface ProjectFrontier {
  shipped_frontier: string | null;
  shipped_frontier_updated_at: string | null;
  frontier_warning?: string | null;
}

export async function getWaveTimeline(projectId: string, waveNum: number): Promise<AgentHubEvent[]> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/ship/waves/${waveNum}/timeline`,
  );
  return checkResponse<AgentHubEvent[]>(response);
}

// ---------------------------------------------------------------------------
// Composite Workspace API (Phase 9)
// ---------------------------------------------------------------------------

export type WorkspaceStatus =
  | 'draft' | 'composing' | 'conflicted' | 'test_failed'
  | 'preview_ready' | 'blessed' | 'snapshot_candidate' | 'discarded';

export interface CompositeWorkspace {
  id: string;
  project_id: string;
  base_root_hash: string | null;
  selected_attempt_ids: string[];
  selected_leaf_hashes: string[];
  status: WorkspaceStatus;
  composed_commit_hash: string | null;
  short_composed_hash: string | null;
  conflict_summary: string | null;
  changed_files: string[];
  summary: string | null;
  test_status: string | null;
  test_output?: string | null;
  preview_url: string | null;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CompatibilityIssue {
  attempt_id: string;
  level: 'error' | 'warning';
  message: string;
}

export interface CompatibilityReport {
  ok: boolean;
  issues: CompatibilityIssue[];
  selected_attempts: {
    attempt_id: string;
    ticket_id: string;
    commit_hash: string | null;
    base_hash: string | null;
    wave_num: number;
    status: string;
    summary: string | null;
    stale: boolean | null;
  }[];
  dep_order: string[];
}

export async function getWorkspaces(projectId: string): Promise<CompositeWorkspace[]> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/workspaces`);
  return checkResponse<CompositeWorkspace[]>(response);
}

export async function createWorkspace(
  projectId: string,
  attemptIds: string[],
  createdBy?: string,
): Promise<CompositeWorkspace> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attempt_ids: attemptIds, created_by: createdBy }),
  });
  return checkResponse<CompositeWorkspace>(response);
}

export async function analyzeCompatibility(
  projectId: string,
  attemptIds: string[],
): Promise<CompatibilityReport> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/workspaces/analyze`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attempt_ids: attemptIds }),
  });
  return checkResponse<CompatibilityReport>(response);
}

export async function getWorkspace(projectId: string, wsId: string): Promise<CompositeWorkspace> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/workspaces/${wsId}?include_test_output=true`,
  );
  return checkResponse<CompositeWorkspace>(response);
}

export async function composeWorkspace(projectId: string, wsId: string): Promise<CompositeWorkspace> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/workspaces/${wsId}/compose`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  return checkResponse<CompositeWorkspace>(response);
}

export async function blessWorkspace(projectId: string, wsId: string): Promise<CompositeWorkspace> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/workspaces/${wsId}/bless`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  return checkResponse<CompositeWorkspace>(response);
}

export async function snapshotWorkspace(projectId: string, wsId: string): Promise<CompositeWorkspace> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/workspaces/${wsId}/snapshot`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  return checkResponse<{ workspace: CompositeWorkspace }>(response).then(r => r.workspace);
}

export async function promoteWorkspace(
  projectId: string,
  wsId: string,
): Promise<{ workspace: CompositeWorkspace; ship_run: ShipRun }> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/workspaces/${wsId}/promote`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  return checkResponse(response);
}

export async function discardWorkspace(projectId: string, wsId: string): Promise<CompositeWorkspace> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/workspaces/${wsId}/discard`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
  );
  return checkResponse<CompositeWorkspace>(response);
}

export async function rejectAttempt(
  projectId: string,
  ticketId: string,
  attemptId: string,
  reason?: string,
): Promise<TicketAttempt> {
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/tickets/${ticketId}/attempts/${attemptId}/reject`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    },
  );
  return checkResponse<TicketAttempt>(response);
}

/** Execution readiness: required env vars set so a ticket can be run. */
export interface ReadyMissing {
  key: string;
  label: string;
}

export interface ReadyResponse {
  ready: boolean;
  missing: ReadyMissing[];
  features?: {
    composite_workspace?: boolean;
  };
}

export async function getExecutionReady(): Promise<ReadyResponse> {
  const response = await fetch(`${API_URL}/api/ready`);
  return checkResponse<ReadyResponse>(response);
}

export interface StartProjectResponse {
  queued: number;
  dispatched: number;
  message: string;
}

/** Move all backlog tickets to queued and immediately dispatch those with no unfinished deps. */
export async function startProject(projectId: string): Promise<StartProjectResponse> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return checkResponse<StartProjectResponse>(response);
}
