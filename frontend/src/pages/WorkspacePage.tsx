/**
 * Composite Workspace — Phase 9
 *
 * A lab-grade workspace surface: select AgentHub leaves, compose a candidate
 * codebase state, run tests, preview, bless, create a Snapshot candidate,
 * or promote through the compatibility ShipRun path.
 *
 * Labels (plan 9.7):
 *   Composite Preview  — preview_ready
 *   Blessed Candidate  — blessed
 *   Snapshot Candidate — snapshot_candidate
 *   Promoted for Export — (promoted)
 *
 * This surface does NOT imply production. A composite is a possible future
 * until it passes the configured export/deploy policy (Phase 14+).
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Checkbox,
  Chip,
  CircularProgress,
  Collapse,
  Divider,
  FormControlLabel,
  IconButton,
  List,
  ListItem,
  ListItemText,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import {
  getProject,
  getWorkspaces,
  createWorkspace,
  analyzeCompatibility,
  getWorkspace,
  composeWorkspace,
  blessWorkspace,
  snapshotWorkspace,
  promoteWorkspace,
  discardWorkspace,
  getTicketAttempts,
  getTickets,
  type Project,
  type CompositeWorkspace,
  type CompatibilityReport,
  type TicketAttempt,
} from '../utils/api';

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const STATUS_COLOR: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  draft: 'default',
  composing: 'info',
  conflicted: 'error',
  test_failed: 'error',
  preview_ready: 'success',
  blessed: 'success',
  snapshot_candidate: 'warning',
  discarded: 'default',
};

const STATUS_LABEL: Record<string, string> = {
  draft: 'Draft',
  composing: 'Composing…',
  conflicted: 'Conflict',
  test_failed: 'Tests Failed',
  preview_ready: 'Composite Preview',
  blessed: 'Blessed Candidate',
  snapshot_candidate: 'Snapshot Candidate',
  discarded: 'Discarded',
};

// ---------------------------------------------------------------------------
// Workspace detail panel
// ---------------------------------------------------------------------------

function WorkspacePanel({
  projectId,
  wsId,
  onClose,
  onUpdate,
}: {
  projectId: string;
  wsId: string;
  onClose: () => void;
  onUpdate: (ws: CompositeWorkspace) => void;
}) {
  const [ws, setWs] = useState<CompositeWorkspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);
  const [testExpanded, setTestExpanded] = useState(false);
  const [filesExpanded, setFilesExpanded] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await getWorkspace(projectId, wsId);
      setWs(data);
      onUpdate(data);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId, wsId, onUpdate]);

  useEffect(() => { load(); }, [load]);

  // Poll while composing
  useEffect(() => {
    if (ws?.status !== 'composing') return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [ws?.status, load]);

  const act = async (label: string, fn: () => Promise<any>) => {
    setActing(label);
    setError(null);
    try {
      const result = await fn();
      const updated = result?.workspace || result;
      setWs(updated);
      onUpdate(updated);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActing(null);
    }
  };

  if (loading) return <Box sx={{ p: 3, textAlign: 'center' }}><CircularProgress size={24} /></Box>;
  if (!ws) return null;

  const canCompose = ['draft', 'conflicted', 'test_failed'].includes(ws.status);
  const canBless = ['preview_ready', 'blessed'].includes(ws.status);
  const canSnapshot = ['preview_ready', 'blessed'].includes(ws.status);
  const canPromote = ['preview_ready', 'blessed', 'snapshot_candidate'].includes(ws.status);
  const canDiscard = !['discarded'].includes(ws.status);

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip
            label={STATUS_LABEL[ws.status] ?? ws.status}
            color={STATUS_COLOR[ws.status] ?? 'default'}
            size="small"
          />
          {ws.short_composed_hash && (
            <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
              {ws.short_composed_hash}
            </Typography>
          )}
        </Stack>
        <Stack direction="row" spacing={1}>
          <Tooltip title="Refresh"><IconButton size="small" onClick={load}><RefreshIcon fontSize="small" /></IconButton></Tooltip>
          <Button size="small" onClick={onClose}>Close</Button>
        </Stack>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {/* Status description (plan 9.7 labels) */}
      {ws.status === 'blessed' && (
        <Alert severity="success" sx={{ mb: 2 }}>
          This is the <strong>Blessed Candidate</strong> — the preferred codebase state.
          Agents building new tickets will start from this composite's commit hash.
          Blessing does not imply production or deployment.
        </Alert>
      )}
      {ws.status === 'snapshot_candidate' && (
        <Alert severity="info" sx={{ mb: 2 }}>
          <strong>Snapshot Candidate</strong> — frozen for export/deploy policy evaluation.
          Phase 14 will add the Verification Engine to finalise snapshot criteria.
        </Alert>
      )}

      {/* Actions */}
      <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 2 }}>
        {canCompose && (
          <Button
            variant="contained" size="small"
            disabled={!!acting}
            onClick={() => act('compose', () => composeWorkspace(projectId, wsId))}
          >
            {acting === 'compose' ? 'Composing…' : ws.status === 'draft' ? 'Compose' : 'Recompose'}
          </Button>
        )}
        {canBless && (
          <Button
            variant="contained" color="success" size="small"
            disabled={!!acting}
            onClick={() => act('bless', () => blessWorkspace(projectId, wsId))}
          >
            {ws.status === 'blessed' ? 'Blessed ✓' : 'Bless Candidate'}
          </Button>
        )}
        {canSnapshot && (
          <Button
            variant="outlined" size="small"
            disabled={!!acting}
            onClick={() => act('snapshot', () => snapshotWorkspace(projectId, wsId))}
          >
            Create Snapshot Candidate
          </Button>
        )}
        {canPromote && (
          <Button
            variant="outlined" color="warning" size="small"
            disabled={!!acting}
            onClick={() => act('promote', () => promoteWorkspace(projectId, wsId))}
          >
            Promote for Export → ShipRun
          </Button>
        )}
        {canDiscard && (
          <Button
            variant="outlined" color="error" size="small"
            disabled={!!acting}
            onClick={() => act('discard', () => discardWorkspace(projectId, wsId))}
          >
            Discard
          </Button>
        )}
      </Stack>

      {ws.status === 'composing' && (
        <Alert severity="info" sx={{ mb: 2 }}>
          Composition in progress — coordinator is running the workspace composer…
        </Alert>
      )}

      {/* Conflict / error */}
      {ws.conflict_summary && (
        <Alert severity="error" sx={{ mb: 2 }}>
          {ws.conflict_summary}
        </Alert>
      )}

      {/* Changed files */}
      {ws.changed_files && ws.changed_files.length > 0 && (
        <Box sx={{ mb: 2 }}>
          <Stack direction="row" alignItems="center" spacing={0.5}>
            <Typography variant="subtitle2">
              {ws.changed_files.length} file{ws.changed_files.length !== 1 ? 's' : ''} changed
            </Typography>
            <IconButton size="small" onClick={() => setFilesExpanded(v => !v)}>
              {filesExpanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
            </IconButton>
          </Stack>
          <Collapse in={filesExpanded}>
            <Box component="pre" sx={{ fontSize: '0.7rem', bgcolor: 'background.default', p: 1, borderRadius: 1, maxHeight: 200, overflow: 'auto', mt: 0.5 }}>
              {ws.changed_files.join('\n')}
            </Box>
          </Collapse>
        </Box>
      )}

      {/* Test output */}
      {ws.test_status && (
        <Box sx={{ mb: 2 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Chip
              label={`Tests: ${ws.test_status}`}
              size="small"
              color={ws.test_status === 'passed' ? 'success' : ws.test_status === 'failed' ? 'error' : 'default'}
              variant="outlined"
            />
            {ws.test_status !== 'skipped' && (
              <IconButton size="small" onClick={() => setTestExpanded(v => !v)}>
                {testExpanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
              </IconButton>
            )}
          </Stack>
          <Collapse in={testExpanded}>
            <Stack direction="row" spacing={0.5} alignItems="flex-start" sx={{ mt: 0.5 }}>
              <Box component="pre" sx={{ flex: 1, fontSize: '0.65rem', bgcolor: 'background.default', p: 1, borderRadius: 1, maxHeight: 300, overflow: 'auto' }}>
                {ws.test_output || '(no output)'}
              </Box>
              <IconButton size="small" onClick={() => navigator.clipboard.writeText(ws.test_output || '')}>
                <ContentCopyIcon fontSize="small" />
              </IconButton>
            </Stack>
          </Collapse>
        </Box>
      )}

      {/* Selected attempts */}
      <Typography variant="subtitle2" gutterBottom>
        Selected attempts ({ws.selected_attempt_ids.length})
      </Typography>
      <Typography variant="caption" color="text.secondary">
        {ws.selected_leaf_hashes.slice(0, 4).map(h => h.slice(0, 10)).join(' · ')}
        {ws.selected_leaf_hashes.length > 4 ? ` +${ws.selected_leaf_hashes.length - 4} more` : ''}
      </Typography>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Leaf selector
// ---------------------------------------------------------------------------

function LeafSelector({
  projectId,
  onCompose,
}: {
  projectId: string;
  onCompose: (wsId: string) => void;
}) {
  const [attempts, setAttempts] = useState<TicketAttempt[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [report, setReport] = useState<CompatibilityReport | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadAttempts = async () => {
      try {
        const tickets = await getTickets(projectId);
        // Load all ticket attempts in parallel
        const results = await Promise.all(
          tickets.map(t => getTicketAttempts(projectId, t.id))
        );
        const all = results
          .flat()
          .filter(a => ['accepted', 'composed', 'release_pr_open', 'shipped'].includes(a.status));
        setAttempts(all);
      } catch (e: any) {
        setError(e.message);
      }
    };
    loadAttempts();
  }, [projectId]);

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    setReport(null);
  };

  const handleAnalyze = async () => {
    if (selected.size === 0) return;
    setAnalyzing(true);
    setError(null);
    try {
      const r = await analyzeCompatibility(projectId, [...selected]);
      setReport(r);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleCreate = async () => {
    if (selected.size === 0) return;
    setCreating(true);
    setError(null);
    try {
      const ws = await createWorkspace(projectId, [...selected]);
      const composed = await composeWorkspace(projectId, ws.id);
      onCompose(composed.id);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <Box>
      <Typography variant="h6" gutterBottom>Leaf Selector</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Select accepted attempts to compose into a candidate codebase state.
      </Typography>

      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {attempts.length === 0 && (
        <Typography variant="body2" color="text.secondary">
          No accepted attempts found. Complete tickets with agents first.
        </Typography>
      )}

      <List dense disablePadding>
        {attempts.map(a => (
          <ListItem key={a.id} disablePadding>
            <FormControlLabel
              control={
                <Checkbox
                  size="small"
                  checked={selected.has(a.id)}
                  onChange={() => toggle(a.id)}
                />
              }
              label={
                <Stack direction="row" spacing={1} alignItems="center">
                  <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                    {a.short_commit_hash || '?'}
                  </Typography>
                  <Chip label={`wave ${a.wave_num}`} size="small" variant="outlined" sx={{ fontSize: '0.6rem' }} />
                  {a.stale && <Chip label="stale" size="small" color="warning" sx={{ fontSize: '0.6rem' }} />}
                  <Typography variant="caption" color="text.secondary" noWrap sx={{ maxWidth: 200 }}>
                    {a.summary?.slice(0, 60)}
                  </Typography>
                </Stack>
              }
              sx={{ width: '100%', m: 0, py: 0.5 }}
            />
          </ListItem>
        ))}
      </List>

      {/* Compatibility report */}
      {report && (
        <Box sx={{ mt: 2 }}>
          <Alert severity={report.ok ? 'success' : report.issues.some(i => i.level === 'error') ? 'error' : 'warning'}>
            {report.ok ? 'No compatibility issues found.' : `${report.issues.length} issue(s) found.`}
          </Alert>
          {report.issues.length > 0 && (
            <List dense sx={{ mt: 1 }}>
              {report.issues.map((issue, i) => (
                <ListItem key={i} disablePadding sx={{ py: 0.25 }}>
                  <ListItemText
                    primary={
                      <Stack direction="row" spacing={0.75} alignItems="flex-start">
                        <Chip
                          label={issue.level}
                          size="small"
                          color={issue.level === 'error' ? 'error' : 'warning'}
                          sx={{ fontSize: '0.6rem', flexShrink: 0 }}
                        />
                        <Typography variant="caption">{issue.message}</Typography>
                      </Stack>
                    }
                  />
                </ListItem>
              ))}
            </List>
          )}
        </Box>
      )}

      <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
        <Button
          size="small" variant="outlined"
          disabled={selected.size === 0 || analyzing}
          onClick={handleAnalyze}
        >
          {analyzing ? <CircularProgress size={14} /> : 'Analyze'}
        </Button>
        <Button
          size="small" variant="contained"
          disabled={selected.size === 0 || creating || (report !== null && !report.ok && report.issues.some(i => i.level === 'error'))}
          onClick={handleCreate}
        >
          {creating ? 'Creating…' : 'Compose Selected'}
        </Button>
      </Stack>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const WorkspacePage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [workspaces, setWorkspaces] = useState<CompositeWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedWs, setSelectedWs] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      const [proj, wss] = await Promise.all([getProject(projectId), getWorkspaces(projectId)]);
      setProject(proj);
      setWorkspaces(wss);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  const handleWorkspaceUpdate = (ws: CompositeWorkspace) => {
    setWorkspaces(prev => {
      const idx = prev.findIndex(w => w.id === ws.id);
      if (idx === -1) return [ws, ...prev];
      const next = [...prev];
      next[idx] = ws;
      return next;
    });
  };

  if (loading) return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
      <CircularProgress />
    </Box>
  );

  const blessed = workspaces.find(w => w.status === 'blessed');
  const active = workspaces.filter(w => !['discarded'].includes(w.status));

  return (
    <Box sx={{ maxWidth: 1200, mx: 'auto', width: '100%' }}>
      {/* Header */}
      <Paper sx={{ p: { xs: 2, md: 3 }, mb: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
        <Stack direction={{ xs: 'column', md: 'row' }} alignItems={{ xs: 'flex-start', md: 'center' }} justifyContent="space-between" spacing={2}>
          <Box>
            <Typography variant="h4">Workspace</Typography>
            {project && <Typography color="text.secondary" sx={{ mt: 0.5 }}>{project.name}</Typography>}
            <Typography variant="caption" color="text.secondary">
              Lab-grade composite workspace · Compose, preview, bless, or promote possible codebase states.
              Blessing ≠ production.
            </Typography>
          </Box>
          <Stack direction="row" spacing={2} alignItems="center">
            {blessed && (
              <Chip label={`Blessed: ${blessed.short_composed_hash || blessed.id.slice(0, 8)}`} color="success" size="small" />
            )}
            <Tooltip title="Refresh">
              <IconButton onClick={load}><RefreshIcon /></IconButton>
            </Tooltip>
          </Stack>
        </Stack>
      </Paper>

      <Box sx={{ display: 'flex', gap: 3, alignItems: 'flex-start' }}>
        {/* Left: leaf selector + workspace list */}
        <Box sx={{ flex: selectedWs ? '0 0 380px' : 1, minWidth: 0 }}>
          {/* Leaf selector */}
          <Paper sx={{ p: 2, mb: 2, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
            {projectId && (
              <LeafSelector
                projectId={projectId}
                onCompose={(wsId) => {
                  load();
                  setSelectedWs(wsId);
                }}
              />
            )}
          </Paper>

          {/* Workspace list */}
          {active.length > 0 && (
            <Paper sx={{ p: 2, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
              <Typography variant="subtitle2" gutterBottom>Workspaces</Typography>
              <Stack spacing={1}>
                {active.map(ws => (
                  <Card
                    key={ws.id}
                    onClick={() => setSelectedWs(prev => prev === ws.id ? null : ws.id)}
                    sx={{
                      cursor: 'pointer',
                      border: selectedWs === ws.id ? '2px solid' : '1px solid rgba(148,163,184,0.3)',
                      borderColor: selectedWs === ws.id ? 'primary.main' : undefined,
                      '&:hover': { borderColor: 'primary.light' },
                    }}
                  >
                    <CardContent sx={{ py: 1, '&:last-child': { pb: 1 } }}>
                      <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
                        <Stack direction="row" spacing={0.75} alignItems="center">
                          <Chip label={STATUS_LABEL[ws.status] ?? ws.status} color={STATUS_COLOR[ws.status] ?? 'default'} size="small" />
                          {ws.short_composed_hash && (
                            <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{ws.short_composed_hash}</Typography>
                          )}
                        </Stack>
                        <Typography variant="caption" color="text.secondary">
                          {ws.selected_attempt_ids.length} attempt{ws.selected_attempt_ids.length !== 1 ? 's' : ''}
                        </Typography>
                      </Stack>
                      {ws.changed_files?.length > 0 && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                          {ws.changed_files.length} files changed
                        </Typography>
                      )}
                    </CardContent>
                  </Card>
                ))}
              </Stack>
            </Paper>
          )}
        </Box>

        {/* Right: workspace detail */}
        {selectedWs && projectId && (
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Paper sx={{ p: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
              <WorkspacePanel
                projectId={projectId}
                wsId={selectedWs}
                onClose={() => setSelectedWs(null)}
                onUpdate={handleWorkspaceUpdate}
              />
            </Paper>
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default WorkspacePage;
