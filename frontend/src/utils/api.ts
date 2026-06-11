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
  /** Canonical accepted AgentHub frontier id for future ticket execution. */
  accepted_frontier_id?: string | null;
  /** Last shipped main commit hash. All new agent work builds on top of this. */
  shipped_frontier?: string | null;
  shipped_frontier_updated_at?: string | null;
  /** Set when the project lacks an explicit canonical DAG frontier. */
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
  promotion_candidate_id: string | null;
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

export interface CandidateMembershipTicket {
  id: string;
  title: string;
  column_id: string;
  depends_on_ticket_ids: string[];
}

export interface CandidateMembership {
  attempts: TicketAttempt[];
  tickets: CandidateMembershipTicket[];
  commit_hashes: string[];
  legacy_wave_num: number | null;
}

export interface PromotionCandidate {
  id: string;
  project_id: string;
  selected_attempt_ids: string[];
  selected_leaf_hashes: string[];
  base_root_hash: string | null;
  status: string;
  validation_summary: Record<string, unknown>;
  conflict_summary: string | null;
  composed_commit_hash: string | null;
  created_at: string | null;
  updated_at: string | null;
  attempts?: Array<{
    id: string;
    ticket_id: string;
    status: string;
    agenthub_commit_hash: string;
    base_hash: string | null;
    attempt_num: number;
  }>;
}

export interface ShipRunDetail extends ShipRun {
  candidate: PromotionCandidate | null;
  membership: CandidateMembership | null;
  validation_errors: string[];
  wave_tickets: CandidateMembershipTicket[];
  commit_hashes: string[];
}

export interface PromotionCandidateDetail extends PromotionCandidate {
  latest_ship_run: ShipRunDetail | null;
  membership: CandidateMembership;
  validation_errors: string[];
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

export type EvidenceTargetType = 'attempt' | 'ship_run' | 'composite_workspace' | 'snapshot';
export type EvidenceStatus = 'collecting' | 'passed' | 'failed' | 'warning' | 'incomplete';
export type EvidenceRiskLevel = 'low' | 'medium' | 'high' | 'unknown';
export type EvidenceCheckStatus = 'passed' | 'failed' | 'warning' | 'skipped';
export type EvidenceArtifactKind = 'log' | 'report' | 'trace' | 'screenshot' | 'video' | 'diff' | 'coverage' | 'other';
export type EvidenceRunType = 'command' | 'suite' | 'browser' | 'replay' | 'llm_review' | 'test_adequacy' | 'mutation' | 'property';
export type EvidenceRunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';

export interface EvidenceArtifact {
  kind: EvidenceArtifactKind;
  label?: string;
  url?: string;
  path?: string;
  exists?: boolean;
}

export interface EvidenceSandboxConfig {
  enabled?: boolean;
  inherit_env?: boolean;
  env?: Record<string, string>;
}

export interface VerificationPolicy {
  required_checks: string[];
  optional_checks: string[];
  required_llm_reviewers: string[];
  block_on: string[];
  check_suites: Array<{
    check_type: string;
    command: string | string[];
    cwd?: string;
    timeout_seconds?: number;
    tool_name?: string;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  }>;
}

export interface EvidenceCheck {
  id: string;
  evidence_bundle_id: string;
  check_type: string;
  status: EvidenceCheckStatus;
  tool_name: string | null;
  command: string | null;
  output: string | null;
  artifact_url: string | null;
  metadata: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface EvidenceBundle {
  id: string;
  project_id: string;
  target_type: EvidenceTargetType;
  target_id: string;
  base_hash: string | null;
  candidate_hash: string | null;
  selected_attempt_ids: string[];
  selected_leaf_hashes: string[];
  status: EvidenceStatus;
  risk_level: EvidenceRiskLevel;
  summary: string | null;
  check_counts: Record<string, number>;
  checks?: EvidenceCheck[];
  created_at: string | null;
  updated_at: string | null;
}

export interface EvidencePolicyEvaluation {
  allowed: boolean;
  target_type: EvidenceTargetType;
  target_id: string;
  policy: VerificationPolicy;
  bundle: EvidenceBundle | null;
  required_checks: Record<string, { status: 'passed' | 'failed' | 'missing' | 'waived'; passed: boolean; waiver?: EvidenceCheck }>;
  required_llm_reviewers: Record<string, { status: 'passed' | 'failed' | 'warning' | 'missing'; passed: boolean; check?: EvidenceCheck }>;
  human_approval: EvidenceCheck | null;
  reasons: string[];
}

export interface EvidenceLlmFinding {
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical' | 'warning';
  path?: string | null;
  line?: number | string | null;
  symbol?: string | null;
  claim: string;
  evidence?: string | null;
  suggested_fix?: string | null;
  blocking?: boolean;
  confidence?: number | string | null;
}

export interface EvidenceTestAdequacyFinding {
  severity: 'info' | 'low' | 'medium' | 'high' | 'critical' | 'warning';
  criterion?: string | null;
  test_path?: string | null;
  covered?: boolean;
  claim: string;
  evidence?: string | null;
  suggested_fix?: string | null;
  blocking?: boolean;
  confidence?: number | string | null;
  weakened_existing_tests?: boolean;
}

export interface EvidenceRun {
  id: string;
  project_id: string;
  evidence_bundle_id: string | null;
  run_type: EvidenceRunType;
  status: EvidenceRunStatus;
  target_type: EvidenceTargetType;
  target_id: string;
  check_type: string | null;
  request_data: Record<string, unknown>;
  error: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  bundle?: EvidenceBundle;
}

export interface EvidenceExternalCheck {
  check_type?: string;
  status?: EvidenceCheckStatus;
  tool_name?: string;
  command?: string;
  output?: string;
  artifact_url?: string;
  metadata?: Record<string, unknown>;
  artifacts?: EvidenceArtifact[];
  findings?: EvidenceLlmFinding[] | EvidenceTestAdequacyFinding[];
  started_at?: string;
  finished_at?: string;
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
  accepted_frontier_id?: string | null;
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
  accepted_frontier_id?: string | null;
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

export async function getShipCandidates(projectId: string): Promise<PromotionCandidate[]> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/candidates`);
  return checkResponse<PromotionCandidate[]>(response);
}

export async function getShipCandidateDetail(projectId: string, candidateId: string): Promise<PromotionCandidateDetail> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/candidates/${candidateId}`);
  return checkResponse<PromotionCandidateDetail>(response);
}

export async function composeShipCandidate(projectId: string, candidateId: string): Promise<ShipRunDetail> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/candidates/${candidateId}/compose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return checkResponse<ShipRunDetail>(response);
}

export async function getShipRun(projectId: string, runId: string): Promise<ShipRunDetail> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/runs/${runId}`);
  return checkResponse<ShipRunDetail>(response);
}

export async function shipRun(projectId: string, runId: string): Promise<ShipRunDetail> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/runs/${runId}/ship`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return checkResponse<ShipRunDetail>(response);
}

export async function shipCandidate(projectId: string, candidateId: string): Promise<ShipRunDetail> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/ship/candidates/${candidateId}/ship`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  return checkResponse<ShipRunDetail>(response);
}

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

async function sendWaveFeedback(
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

export async function sendCandidateFeedback(
  projectId: string,
  candidateId: string,
  message: string,
  targetTicketId?: string,
): Promise<void> {
  const detail = await getShipCandidateDetail(projectId, candidateId);
  const legacyWaveNum = detail.membership?.legacy_wave_num;
  if (legacyWaveNum === null || legacyWaveNum === undefined) {
    throw new Error('Candidate feedback is not supported by this backend yet.');
  }
  await sendWaveFeedback(projectId, legacyWaveNum, message, targetTicketId);
}

export async function getTicketAttempts(
  projectId: string,
  ticketId: string,
  includeTestOutput = false,
): Promise<TicketAttempt[]> {
  const query = includeTestOutput ? '?include_test_output=true' : '';
  const response = await fetch(
    `${API_URL}/api/projects/${projectId}/tickets/${ticketId}/attempts${query}`,
  );
  return checkResponse<TicketAttempt[]>(response);
}

export async function getTicketAttempt(
  projectId: string,
  ticketId: string,
  attemptId: string,
  includeTestOutput = false,
): Promise<TicketAttempt> {
  const attempts = await getTicketAttempts(projectId, ticketId, includeTestOutput);
  const attempt = attempts.find(a => a.id === attemptId);
  if (!attempt) {
    throw new Error('Attempt not found');
  }
  return attempt;
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

// ---------------------------------------------------------------------------
// Verification Evidence API (Phase 14)
// ---------------------------------------------------------------------------

export async function getEvidencePolicy(
  projectId: string,
  targetType: EvidenceTargetType,
  targetId: string,
): Promise<EvidencePolicyEvaluation> {
  const params = new URLSearchParams({ target_type: targetType, target_id: targetId });
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/policy?${params}`);
  return checkResponse<EvidencePolicyEvaluation>(response);
}

export async function getEvidence(
  projectId: string,
  filters?: { target_type?: EvidenceTargetType; target_id?: string; check_type?: string },
): Promise<EvidenceBundle[]> {
  const params = new URLSearchParams();
  if (filters?.target_type) params.set('target_type', filters.target_type);
  if (filters?.target_id) params.set('target_id', filters.target_id);
  if (filters?.check_type) params.set('check_type', filters.check_type);
  const query = params.toString();
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence${query ? `?${query}` : ''}`);
  return checkResponse<EvidenceBundle[]>(response);
}

export async function collectEvidence(
  projectId: string,
  data: { target_type: EvidenceTargetType; target_id: string; check_type?: string },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/collect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runCommandEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    check_type: string;
    command: string | string[];
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-command`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runEvidenceSuite(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    timeout_seconds?: number;
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-suite`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runBrowserEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    check_type?: string;
    command: string | string[];
    preview_url?: string;
    preview_required?: boolean;
    preview_command?: string | string[];
    preview_launch_required?: boolean;
    preview_ready_timeout_seconds?: number;
    preview_supervision_enabled?: boolean;
    auto_detect_preview_command?: boolean;
    report_path?: string;
    results_path?: string;
    trace_path?: string;
    screenshot_path?: string;
    video_path?: string;
    retry_count?: number;
    shard?: { index: number; total: number };
    console_errors?: string[];
    network_failures?: string[];
    failure_artifacts_only?: boolean;
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-browser`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runReplayEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    check_type?: string;
    command: string | string[];
    traffic_path?: string;
    contract_path?: string;
    base_url?: string;
    stable_url?: string;
    candidate_url?: string;
    base_url_required?: boolean;
    candidate_url_required?: boolean;
    contract_validation_required?: boolean;
    contract_parse_required?: boolean;
    traffic_source?: string;
    sample_count?: number;
    traffic_parse_required?: boolean;
    generate_replay_manifest?: boolean;
    replay_manifest_path?: string;
    contract_compatible?: boolean;
    compared_endpoints?: string[];
    status_code_regressions?: string[];
    schema_regressions?: string[];
    auth_regressions?: string[];
    behavior_regressions?: string[];
    diff_path?: string;
    report_path?: string;
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-replay`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runMutationEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    command: string | string[];
    check_type?: string;
    changed_paths?: string[];
    mutation_threshold?: number;
    report_path?: string;
    html_report_path?: string;
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-mutation`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runPropertyEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    command: string | string[];
    check_type?: string;
    properties?: string[];
    generated_cases?: number;
    report_path?: string;
    examples_path?: string;
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-property`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runLlmReviewEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    reviewer: string;
    command?: string | string[];
    findings?: EvidenceLlmFinding[];
    model?: string;
    prompt_version?: string;
    report_path?: string;
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-llm-review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function runTestAdequacyEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    command?: string | string[];
    findings?: EvidenceTestAdequacyFinding[];
    generated_test_paths?: string[];
    acceptance_criteria?: string[];
    generate_candidate_tests?: boolean;
    write_generated_tests?: boolean;
    overwrite_generated_tests?: boolean;
    generated_test_prefix?: string;
    generated_test_framework?: string;
    generated_test_bodies?: Record<string, string>;
    report_path?: string;
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/run-test-adequacy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function getEvidenceRuns(
  projectId: string,
  filters?: { status?: EvidenceRunStatus; target_type?: EvidenceTargetType; target_id?: string },
): Promise<EvidenceRun[]> {
  const params = new URLSearchParams();
  if (filters?.status) params.set('status', filters.status);
  if (filters?.target_type) params.set('target_type', filters.target_type);
  if (filters?.target_id) params.set('target_id', filters.target_id);
  const query = params.toString();
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/runs${query ? `?${query}` : ''}`);
  return checkResponse<EvidenceRun[]>(response);
}

export async function queueEvidenceRun(
  projectId: string,
  data: {
    run_type: EvidenceRunType;
    target_type: EvidenceTargetType;
    target_id: string;
    check_type?: string;
    command?: string | string[];
    preview_url?: string;
    preview_required?: boolean;
    preview_command?: string | string[];
    preview_launch_required?: boolean;
    preview_ready_timeout_seconds?: number;
    preview_supervision_enabled?: boolean;
    auto_detect_preview_command?: boolean;
    report_path?: string;
    results_path?: string;
    trace_path?: string;
    screenshot_path?: string;
    video_path?: string;
    retry_count?: number;
    shard?: { index: number; total: number };
    console_errors?: string[];
    network_failures?: string[];
    failure_artifacts_only?: boolean;
    traffic_path?: string;
    contract_path?: string;
    base_url?: string;
    stable_url?: string;
    candidate_url?: string;
    base_url_required?: boolean;
    candidate_url_required?: boolean;
    contract_validation_required?: boolean;
    contract_parse_required?: boolean;
    traffic_source?: string;
    sample_count?: number;
    traffic_parse_required?: boolean;
    generate_replay_manifest?: boolean;
    replay_manifest_path?: string;
    contract_compatible?: boolean;
    compared_endpoints?: string[];
    status_code_regressions?: string[];
    schema_regressions?: string[];
    auth_regressions?: string[];
    behavior_regressions?: string[];
    diff_path?: string;
    changed_paths?: string[];
    mutation_threshold?: number;
    properties?: string[];
    generated_cases?: number;
    reviewer?: string;
    external_worker?: boolean;
    external_worker_required?: boolean;
    requires_external_worker?: boolean;
    findings?: EvidenceLlmFinding[] | EvidenceTestAdequacyFinding[];
    generated_test_paths?: string[];
    acceptance_criteria?: string[];
    generate_candidate_tests?: boolean;
    write_generated_tests?: boolean;
    overwrite_generated_tests?: boolean;
    generated_test_prefix?: string;
    generated_test_framework?: string;
    generated_test_bodies?: Record<string, string>;
    model?: string;
    prompt_version?: string;
    cwd?: string;
    timeout_seconds?: number;
    artifacts?: EvidenceArtifact[];
    sandbox?: EvidenceSandboxConfig;
  },
): Promise<EvidenceRun> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/runs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceRun>(response);
}

export async function completeExternalEvidenceRun(
  runId: string,
  data: {
    worker_id?: string;
    status?: EvidenceStatus;
    risk_level?: EvidenceRiskLevel;
    summary?: string;
    base_hash?: string;
    candidate_hash?: string;
    selected_attempt_ids?: string[];
    selected_leaf_hashes?: string[];
    checks?: EvidenceExternalCheck[];
    findings?: EvidenceLlmFinding[];
    reviewer?: string;
    model?: string;
    prompt_version?: string;
    output?: string;
    artifacts?: EvidenceArtifact[];
  },
): Promise<{ run: EvidenceRun }> {
  const response = await fetch(`${API_URL}/api/worker/evidence-runs/${runId}/complete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<{ run: EvidenceRun }>(response);
}

export async function failExternalEvidenceRun(
  runId: string,
  data: { error: string },
): Promise<{ run: EvidenceRun }> {
  const response = await fetch(`${API_URL}/api/worker/evidence-runs/${runId}/fail`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<{ run: EvidenceRun }>(response);
}

export async function compareEvidence(
  projectId: string,
  data: {
    target_type: EvidenceTargetType;
    target_id: string;
    base_hash?: string;
    candidate_hash?: string;
    timeout_seconds?: number;
  },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/compare`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceBundle>(response);
}

export async function addEvidenceWaiver(
  projectId: string,
  bundleId: string,
  data: { check_type: string; reason: string; actor?: string },
): Promise<EvidenceCheck> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/${bundleId}/waivers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceCheck>(response);
}

export async function addEvidenceApproval(
  projectId: string,
  bundleId: string,
  data: { actor: string; reason: string },
): Promise<EvidenceCheck> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/${bundleId}/approvals`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  return checkResponse<EvidenceCheck>(response);
}

export async function createEvidenceRepairTicket(
  projectId: string,
  bundleId: string,
  data?: {
    title?: string;
    description?: string;
    column_id?: string;
    priority?: string;
    depends_on_ticket_ids?: string[];
    auto_dispatch_repair?: boolean;
    max_repair_attempts?: number;
    repair_policy?: { auto_dispatch?: boolean; max_attempts?: number };
  },
): Promise<Ticket> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/${bundleId}/repair`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {}),
  });
  return checkResponse<Ticket>(response);
}

export async function rerunEvidenceChecks(
  projectId: string,
  bundleId: string,
  data?: { check_ids?: string[]; timeout_seconds?: number },
): Promise<EvidenceBundle> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/evidence/${bundleId}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data || {}),
  });
  return checkResponse<EvidenceBundle>(response);
}

/** An event posted to an AgentHub channel — execution ledger entry. */
export interface AgentHubEvent {
  id: number;
  channel_id: number;
  agent_id: string;
  parent_id: number | null;
  content: string;
  message?: string;
  event_type?: string;
  metadata?: Record<string, unknown>;
  raw_content?: string;
  structured?: boolean;
  created_at: string;
  /** Channel the event was posted to */
  _channel: string;
  /** 'wave' = wave-level event, 'ticket' = per-ticket event */
  _channel_type: 'wave' | 'ticket';
  /** Title of the ticket this event belongs to (ticket events only) */
  _ticket_title?: string;
  _ticket_id?: string;
}

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
  preview_status: string | null;
  preview_command: string[];
  preview_error: string | null;
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
  preview?: { preview_url?: string; preview_status?: string; preview_command?: string | string[]; preview_error?: string },
): Promise<CompositeWorkspace> {
  const response = await fetch(`${API_URL}/api/projects/${projectId}/workspaces`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ attempt_ids: attemptIds, created_by: createdBy, ...(preview || {}) }),
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
