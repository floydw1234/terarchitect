import React, { useState, useEffect, useCallback } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Box,
  Typography,
  Paper,
  Button,
  Chip,
  Stack,
  Divider,
  CircularProgress,
  Alert,
  TextField,
  Collapse,
  IconButton,
  Tooltip,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import EvidencePanel from '../components/EvidencePanel';
import {
  getProject,
  getShipWaves,
  getShipWaveDetail,
  getWaveTimeline,
  getTicketAttempts,
  composeWave,
  shipWave,
  sendWaveFeedback,
  acceptAttempt,
  rejectAttempt,
  createTicket,
  type Project,
  type WaveSummary,
  type WaveDetail,
  type ShipRun,
  type AgentHubEvent,
  type ProjectFrontier,
  type TicketAttempt,
} from '../utils/api';

// ---------------------------------------------------------------------------
// Status helpers
// ---------------------------------------------------------------------------

const STATUS_COLOR: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  queued: 'info',
  running: 'info',
  compose_failed: 'error',
  failed: 'error',
  ready_to_ship: 'warning',
  shipping: 'warning',
  shipped: 'success',
  done: 'success',
};

const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued',
  running: 'Composing…',
  compose_failed: 'Compose Failed',
  failed: 'Failed',
  ready_to_ship: 'Ready to Ship',
  shipping: 'Shipping…',
  shipped: 'Shipped',
  done: 'Done',
};

const ATTEMPT_COLOR: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  proposed: 'default',
  validating: 'info',
  accepted: 'success',
  rejected: 'error',
  superseded: 'default',
  composed: 'info',
  release_pr_open: 'warning',
  shipped: 'success',
  failed: 'error',
};

const REVIEWABLE_ATTEMPT_STATUSES = new Set(['proposed', 'validating']);

function getAttemptReviewLockReason(
  attempt: TicketAttempt,
  shipRunStatus: string | null | undefined,
): string | null {
  if (shipRunStatus && !['compose_failed', 'failed'].includes(shipRunStatus)) {
    return `This wave is ${STATUS_LABEL[shipRunStatus] ?? shipRunStatus} and attempt review is locked.`;
  }
  if (!REVIEWABLE_ATTEMPT_STATUSES.has(attempt.status)) {
    return 'Only proposed or validating attempts can be reviewed here.';
  }
  return null;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ShipRunPanel({ projectId, run }: { projectId: string; run: ShipRun }) {
  const [secondaryOpen, setSecondaryOpen] = useState(false);
  const hasDiagnosticOutput = !!(run.error || run.test_output || run.test_status);

  return (
    <Paper sx={{ p: 2, mt: 1, bgcolor: 'background.default' }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Typography variant="body2" fontWeight={600}>Ship run</Typography>
        <Chip
          label={STATUS_LABEL[run.status] ?? run.status}
          color={STATUS_COLOR[run.status] ?? 'default'}
          size="small"
        />
        {run.release_pr_url && (
          <Button
            size="small"
            endIcon={<OpenInNewIcon fontSize="small" />}
            href={run.release_pr_url}
            target="_blank"
            rel="noopener noreferrer"
          >
            Release PR #{run.release_pr_number}
          </Button>
        )}
        {run.shipped_commit_hash && (
          <Typography variant="caption" color="text.secondary">
            shipped: {run.shipped_commit_hash.slice(0, 12)}
          </Typography>
        )}
      </Stack>

      {run.release_branch && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
          branch: <code>{run.release_branch}</code>
        </Typography>
      )}

      {run.error && (
        <Alert severity="error" sx={{ mt: 1, fontSize: '0.75rem' }}>
          {run.error}
        </Alert>
      )}

      {hasDiagnosticOutput && (
        <Box sx={{ mt: 1 }}>
          <Stack direction="row" alignItems="center" spacing={0.75} flexWrap="wrap">
            {run.test_status && (
              <Chip
                label={`Tests: ${run.test_status}`}
                size="small"
                color={run.test_status === 'passed' ? 'success' : run.test_status === 'failed' ? 'error' : 'default'}
                variant="outlined"
              />
            )}
            {run.error && (
              <Chip label="Failure details" size="small" color="error" variant="outlined" />
            )}
          </Stack>
          {(run.error || run.test_output || run.test_status) && (
            <Box
              component="pre"
              sx={{
                mt: 0.75,
                fontSize: '0.72rem',
                bgcolor: 'background.paper',
                p: 1,
                borderRadius: 1,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                maxHeight: 280,
                overflow: 'auto',
              }}
            >
              {run.error || run.test_output || (run.test_status === 'skipped' ? '(no test command configured)' : '(no output)')}
            </Box>
          )}
        </Box>
      )}

      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mt: 1.5 }}>
        <Typography variant="caption" color="text.secondary">
          Optional details
        </Typography>
        <Button
          size="small"
          onClick={() => setSecondaryOpen(v => !v)}
          endIcon={secondaryOpen ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
        >
          {secondaryOpen ? 'Hide' : 'Show'} optional details
        </Button>
      </Stack>

      <Collapse in={secondaryOpen}>
        <Stack spacing={1} sx={{ mt: 1 }}>
          {run.changed_files && run.changed_files.length > 0 && (
            <Box>
              <Typography variant="caption" color="text.secondary">
                {run.changed_files.length} file{run.changed_files.length !== 1 ? 's' : ''} changed
              </Typography>
              <Box
                component="pre"
                sx={{
                  mt: 0.5,
                  fontSize: '0.7rem',
                  bgcolor: 'background.paper',
                  p: 1,
                  borderRadius: 1,
                  maxHeight: 200,
                  overflow: 'auto',
                }}
              >
                {run.changed_files.join('\n')}
              </Box>
            </Box>
          )}

          <EvidencePanel
            projectId={projectId}
            targetType="ship_run"
            targetId={run.id}
            defaultCheckType="integration"
          />
        </Stack>
      </Collapse>
    </Paper>
  );
}

function AttemptReviewCard({
  projectId,
  attempts,
  shipRunStatus,
  onActionComplete,
}: {
  projectId: string;
  attempts: TicketAttempt[];
  shipRunStatus: string | null | undefined;
  onActionComplete: () => Promise<void>;
}) {
  const [busyAttemptId, setBusyAttemptId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const handleAttemptAction = async (attempt: TicketAttempt, action: 'accept' | 'reject') => {
    const lockReason = getAttemptReviewLockReason(attempt, shipRunStatus);
    if (lockReason) {
      setMessage(lockReason);
      return;
    }

    setBusyAttemptId(attempt.id);
    setMessage(null);
    try {
      if (action === 'accept') {
        await acceptAttempt(projectId, attempt.ticket_id, attempt.id);
      } else {
        await rejectAttempt(projectId, attempt.ticket_id, attempt.id, 'Rejected from Ship Room review.');
      }
      setMessage(`${action === 'accept' ? 'Accepted' : 'Rejected'} attempt #${attempt.attempt_num}.`);
      await onActionComplete();
    } catch (e: any) {
      setMessage(e.message);
    } finally {
      setBusyAttemptId(null);
    }
  };

  return (
    <Paper sx={{ p: 1.5, bgcolor: 'background.default' }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 0.75 }}>
        <Typography variant="body2" fontWeight={600}>
          Attempt review
        </Typography>
        <Chip label={`${attempts.length} attempt${attempts.length !== 1 ? 's' : ''}`} size="small" variant="outlined" />
      </Stack>

      {message && (
        <Alert severity={message.includes('locked') ? 'warning' : 'info'} sx={{ mb: 1, py: 0 }}>
          {message}
        </Alert>
      )}

      <Stack spacing={1}>
        {attempts.length === 0 ? (
          <Typography variant="caption" color="text.secondary">
            No attempts to review yet.
          </Typography>
        ) : (
          attempts.map(attempt => {
            const lockReason = getAttemptReviewLockReason(attempt, shipRunStatus);
            const busy = busyAttemptId === attempt.id;
            return (
              <Paper key={attempt.id} variant="outlined" sx={{ p: 1.25, bgcolor: 'background.paper' }}>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                  <Chip
                    label={attempt.status}
                    size="small"
                    color={ATTEMPT_COLOR[attempt.status] ?? 'default'}
                  />
                  {attempt.short_commit_hash && (
                    <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                      {attempt.short_commit_hash}
                    </Typography>
                  )}
                  <Typography variant="caption" color="text.secondary">
                    attempt #{attempt.attempt_num}
                  </Typography>
                  {attempt.base_hash && (
                    <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }}>
                      base {attempt.base_hash.slice(0, 12)}
                    </Typography>
                  )}
                  {attempt.test_status && (
                    <Chip
                      label={`tests: ${attempt.test_status}`}
                      size="small"
                      color={attempt.test_status === 'passed' ? 'success' : attempt.test_status === 'failed' ? 'error' : 'default'}
                      variant="outlined"
                    />
                  )}
                </Stack>

                {attempt.summary && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                    {attempt.summary.slice(0, 180)}{attempt.summary.length > 180 ? '…' : ''}
                  </Typography>
                )}

                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mt: 0.75 }}>
                  <Button
                    size="small"
                    variant="contained"
                    aria-label={`Accept attempt #${attempt.attempt_num}`}
                    onClick={() => handleAttemptAction(attempt, 'accept')}
                    disabled={!!lockReason || busy}
                  >
                    {busy ? 'Working…' : 'Accept'}
                  </Button>
                  <Button
                    size="small"
                    variant="outlined"
                    color="error"
                    aria-label={`Reject attempt #${attempt.attempt_num}`}
                    onClick={() => handleAttemptAction(attempt, 'reject')}
                    disabled={!!lockReason || busy}
                  >
                    Reject
                  </Button>
                  {lockReason && (
                    <Typography variant="caption" color="text.secondary">
                      {lockReason}
                    </Typography>
                  )}
                </Stack>
              </Paper>
            );
          })
        )}
      </Stack>
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Channel timeline
// ---------------------------------------------------------------------------

function ChannelTimeline({ projectId, waveNum }: { projectId: string; waveNum: number }) {
  const [posts, setPosts] = useState<AgentHubEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getWaveTimeline(projectId, waveNum)
      .then(setPosts)
      .catch(() => setPosts([]))
      .finally(() => setLoading(false));
  }, [projectId, waveNum]);

  if (loading) return <CircularProgress size={16} sx={{ display: 'block', mx: 'auto', my: 1 }} />;
  if (posts.length === 0) {
    return <Typography variant="caption" color="text.secondary">No events yet.</Typography>;
  }

  return (
    <Stack spacing={0.75}>
      {posts.map(p => (
        <Box
          key={p.id}
          sx={{
            p: 1,
            borderRadius: 1,
            bgcolor: p._channel_type === 'wave' ? 'rgba(34,211,238,0.06)' : 'background.default',
            borderLeft: 3,
            borderLeftColor: p._channel_type === 'wave' ? 'primary.dark' : 'divider',
          }}
        >
          <Stack direction="row" spacing={1} alignItems="flex-start">
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap">
                <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'monospace' }}>
                  {p.agent_id}
                </Typography>
                <Chip
                  label={p._ticket_title ? `#${p._ticket_title.slice(0, 24)}` : p._channel}
                  size="small"
                  variant="outlined"
                  sx={{ height: 14, fontSize: '0.6rem' }}
                />
                {p.event_type && (
                  <Chip
                    label={p.event_type.replace(/_/g, ' ')}
                    size="small"
                    color={p.structured ? 'primary' : 'default'}
                    variant={p.structured ? 'filled' : 'outlined'}
                    sx={{ height: 14, fontSize: '0.6rem' }}
                  />
                )}
                <Typography variant="caption" color="text.secondary">
                  {p.created_at ? new Date(p.created_at).toLocaleTimeString() : ''}
                </Typography>
              </Stack>
              <Typography
                variant="caption"
                sx={{ display: 'block', mt: 0.25, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
              >
                {p.message || p.content}
              </Typography>
            </Box>
          </Stack>
        </Box>
      ))}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Wave detail panel
// ---------------------------------------------------------------------------

function WaveDetailPanel({
  projectId,
  waveNum,
  onClose,
}: {
  projectId: string;
  waveNum: number;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<WaveDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [shipping, setShipping] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [feedbackSending, setFeedbackSending] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [creatingFixTicket, setCreatingFixTicket] = useState(false);
  const [fixTicketCreated, setFixTicketCreated] = useState(false);
  const [ticketAttempts, setTicketAttempts] = useState<Record<string, TicketAttempt[]>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getShipWaveDetail(projectId, waveNum);
      setDetail(d);
      const attemptsByTicket = await Promise.all(
        d.tickets.map(async ticket => {
          const attempts = await getTicketAttempts(projectId, ticket.id, true).catch(() => []);
          return [ticket.id, attempts] as const;
        }),
      );
      setTicketAttempts(Object.fromEntries(attemptsByTicket));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId, waveNum]);

  useEffect(() => { load(); }, [load]);

  const handleCompose = async () => {
    setComposing(true);
    try {
      await composeWave(projectId, waveNum);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setComposing(false);
    }
  };

  const handleShip = async () => {
    setShipping(true);
    try {
      await shipWave(projectId, waveNum);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setShipping(false);
    }
  };

  const handleCreateFixTicket = async () => {
    if (!detail?.ship_run?.error) return;
    setCreatingFixTicket(true);
    try {
      await createTicket(projectId, {
        column_id: 'backlog',
        title: `[wave-${waveNum}] Fix composition failure`,
        description: detail.ship_run.error.slice(0, 2000),
        priority: 'high',
        acceptance_criteria: 'Composition of wave ' + waveNum + ' succeeds without conflicts.',
        associated_node_ids: ['*'],
      });
      setFixTicketCreated(true);
      setTimeout(() => setFixTicketCreated(false), 3000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setCreatingFixTicket(false);
    }
  };

  const handleFeedback = async () => {
    if (!feedback.trim()) return;
    setFeedbackSending(true);
    try {
      await sendWaveFeedback(projectId, waveNum, feedback.trim());
      setFeedback('');
      setFeedbackSent(true);
      setTimeout(() => setFeedbackSent(false), 3000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFeedbackSending(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress size={24} />
      </Box>
    );
  }

  if (error) {
    return <Alert severity="error" sx={{ m: 2 }}>{error}</Alert>;
  }

  if (!detail) return null;

  const shipRun = detail.ship_run;
  const canShip = shipRun?.status === 'ready_to_ship' && !!shipRun.release_pr_number;
  const composeLabel =
    shipRun && ['compose_failed', 'failed'].includes(shipRun.status)
      ? 'Retry compose'
      : 'Compose wave';

  return (
    <Box>
      {/* Header */}
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
        <Typography variant="h6">Wave {waveNum}</Typography>
        <Stack direction="row" spacing={1}>
          <Tooltip title="Refresh">
            <IconButton size="small" onClick={load}><RefreshIcon fontSize="small" /></IconButton>
          </Tooltip>
          <Button size="small" onClick={onClose}>Close</Button>
        </Stack>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 1 }}>{error}</Alert>}

      {/* Staleness warning */}
      {detail.stale_count > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {detail.stale_count} attempt{detail.stale_count !== 1 ? 's are' : ' is'} stale —
          built before the current shipped frontier. Composition will run a conflict check.
        </Alert>
      )}

      {/* Actions */}
      <Stack direction="row" spacing={1} sx={{ mb: 2 }} flexWrap="wrap">
        {detail.can_compose && (
          <Button
            variant="contained"
            size="small"
            onClick={handleCompose}
            disabled={composing}
            startIcon={composing ? <CircularProgress size={12} color="inherit" /> : undefined}
          >
            {composing ? 'Composing…' : composeLabel}
          </Button>
        )}
        {canShip && (
          <Button
            variant="contained"
            color="success"
            size="small"
            onClick={handleShip}
            disabled={shipping}
            startIcon={shipping ? <CircularProgress size={12} color="inherit" /> : undefined}
          >
            {shipping ? 'Shipping…' : 'Ship wave'}
          </Button>
        )}
        {shipRun?.status && ['compose_failed', 'failed'].includes(shipRun.status) && shipRun.error && (
          <Typography variant="caption" color="warning.main" sx={{ alignSelf: 'center' }}>
            Composition failed; see Ship run details below.
          </Typography>
        )}
      </Stack>

      {/* Ship run detail */}
      {shipRun && <ShipRunPanel projectId={projectId} run={shipRun} />}

      <Divider sx={{ my: 2 }} />

      {/* Accepted attempts */}
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }} flexWrap="wrap">
        <Typography variant="subtitle2">
          Accepted attempts ({detail.accepted_attempts.length})
        </Typography>
        {shipRun?.status === 'shipped' && (
          <Chip label="Shipped wave" size="small" color="success" variant="outlined" />
        )}
        {shipRun?.status === 'ready_to_ship' && (
          <Chip label="Ready to ship" size="small" color="warning" variant="outlined" />
        )}
      </Stack>
      {detail.accepted_attempts.length === 0 ? (
        <Typography variant="body2" color="text.secondary">No accepted attempts yet.</Typography>
      ) : (
        <Stack spacing={1} sx={{ mb: 2 }}>
          {detail.accepted_attempts.map(a => (
            <Paper key={a.id} sx={{ p: 1.5, bgcolor: 'background.default' }}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                <Chip
                  label={a.status}
                  size="small"
                  color={ATTEMPT_COLOR[a.status] ?? 'default'}
                />
                {a.short_commit_hash && (
                  <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                    {a.short_commit_hash}
                  </Typography>
                )}
                {a.stale && (
                  <Chip label="stale" size="small" color="warning" variant="outlined" />
                )}
                <Typography variant="caption" color="text.secondary">
                  attempt #{a.attempt_num}
                </Typography>
                <Button
                  component={Link}
                  to={`/projects/${projectId}/tickets/${a.ticket_id}/attempts/${a.id}`}
                  size="small"
                  sx={{ fontSize: '0.65rem' }}
                >
                  Detail
                </Button>
              </Stack>
              {shipRun?.status === 'shipped' && (
                <Typography variant="caption" color="success.main" sx={{ display: 'block', mt: 0.5 }}>
                  Selected attempt from the released wave.
                </Typography>
              )}
              {a.validation_error && (
                <Typography variant="caption" color="error.main" sx={{ display: 'block', mt: 0.5 }}>
                  ⚠ {a.validation_error}
                </Typography>
              )}
              {a.summary && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  {a.summary.slice(0, 200)}{a.summary.length > 200 ? '…' : ''}
                </Typography>
              )}
              <Box sx={{ mt: 0.75 }}>
                <Typography variant="caption" color="text.secondary">
                  Optional evidence
                </Typography>
                <EvidencePanel
                  projectId={projectId}
                  targetType="attempt"
                  targetId={a.id}
                  defaultCheckType="validation"
                />
              </Box>
            </Paper>
          ))}
        </Stack>
      )}

      {/* Tickets */}
      <Typography variant="subtitle2" gutterBottom sx={{ mt: 1 }}>
        Tickets in this wave ({detail.tickets.length})
      </Typography>
      <Stack spacing={1.25} sx={{ mb: 2 }}>
        {detail.tickets.map(t => (
          <Paper key={t.id} data-testid={`ticket-card-${t.id}`} sx={{ p: 1.5, bgcolor: 'background.default' }}>
            <Stack direction="row" spacing={0.75} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
              <Typography variant="body2" fontWeight={600} noWrap sx={{ maxWidth: 320 }}>
                {t.title}
              </Typography>
              <Chip label={t.column_id} size="small" variant="outlined" sx={{ fontSize: '0.65rem' }} />
              {t.latest_attempt && (
                <Chip
                  label={`${t.latest_attempt.status}${t.latest_attempt.short_commit_hash ? ' · ' + t.latest_attempt.short_commit_hash : ''}`}
                  size="small"
                  color={ATTEMPT_COLOR[t.latest_attempt.status] ?? 'default'}
                  sx={{ fontSize: '0.65rem' }}
                />
              )}
            </Stack>
            <AttemptReviewCard
              projectId={projectId}
              attempts={ticketAttempts[t.id] ?? []}
              shipRunStatus={shipRun?.status}
              onActionComplete={load}
            />
          </Paper>
        ))}
      </Stack>

      <Divider sx={{ my: 2 }} />

      {/* Feedback */}
      <Typography variant="subtitle2" gutterBottom>Send feedback to AgentHub</Typography>
      <Stack direction="row" spacing={1} alignItems="flex-start">
        <TextField
          size="small"
          multiline
          minRows={2}
          fullWidth
          placeholder="Describe what needs to change…"
          value={feedback}
          onChange={e => setFeedback(e.target.value)}
        />
        <Button
          variant="outlined"
          size="small"
          onClick={handleFeedback}
          disabled={feedbackSending || !feedback.trim()}
          sx={{ minWidth: 80, alignSelf: 'flex-end' }}
        >
          {feedbackSending ? <CircularProgress size={14} /> : feedbackSent ? 'Sent!' : 'Send'}
        </Button>
      </Stack>

      <Divider sx={{ my: 2 }} />

      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 1 }}>
        <Typography variant="subtitle2">Optional surfaces</Typography>
        <Button
          size="small"
          variant="outlined"
          color="warning"
          onClick={handleCreateFixTicket}
          disabled={creatingFixTicket || fixTicketCreated || !shipRun?.error}
        >
          {fixTicketCreated ? 'Fix ticket created' : creatingFixTicket ? 'Creating…' : 'Create fix ticket'}
        </Button>
      </Stack>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        Timeline, evidence, and the fix-ticket helper are secondary to the release path.
      </Typography>
      <Typography variant="subtitle2" gutterBottom>Event timeline</Typography>
      <ChannelTimeline projectId={projectId} waveNum={waveNum} />
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Wave summary card
// ---------------------------------------------------------------------------

function WaveCard({
  wave,
  selected,
  onSelect,
}: {
  wave: WaveSummary;
  selected: boolean;
  onSelect: (n: number) => void;
}) {
  const run = wave.ship_run;
  const statusColor = run ? (STATUS_COLOR[run.status] ?? 'default') : 'default';
  const statusLabel = run ? (STATUS_LABEL[run.status] ?? run.status) : null;

  return (
    <Paper
      onClick={() => onSelect(wave.wave_num)}
      sx={{
        p: 2,
        cursor: 'pointer',
        border: selected ? '2px solid' : '1px solid rgba(148,163,184,0.45)',
        borderColor: selected ? 'primary.main' : undefined,
        '&:hover': { borderColor: 'primary.light' },
      }}
    >
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
        <Typography variant="subtitle2" fontWeight={600}>
          Wave {wave.wave_num}
        </Typography>
        {statusLabel && (
          <Chip label={statusLabel} color={statusColor} size="small" />
        )}
      </Stack>
      <Typography variant="caption" color="text.secondary">
        {wave.ticket_count} ticket{wave.ticket_count !== 1 ? 's' : ''} ·{' '}
        {wave.accepted_count} accepted
        {!wave.all_done && ' · not all done'}
      </Typography>
      {run?.shipped_commit_hash && (
        <Typography variant="caption" color="success.main" sx={{ display: 'block' }}>
          Shipped · {run.shipped_commit_hash.slice(0, 10)}
        </Typography>
      )}
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const ShipRoomPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [frontier, setFrontier] = useState<ProjectFrontier | null>(null);
  const [waves, setWaves] = useState<WaveSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedWave, setSelectedWave] = useState<number | null>(null);

  const loadWaves = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const [proj, ws] = await Promise.all([
        getProject(projectId),
        getShipWaves(projectId),
      ]);
      setProject(proj);
      setWaves(ws);
      setFrontier({
        shipped_frontier: proj.shipped_frontier ?? null,
        shipped_frontier_updated_at: proj.shipped_frontier_updated_at ?? null,
        frontier_warning: proj.frontier_warning ?? null,
      });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { loadWaves(); }, [loadWaves]);

  // Group waves by status for display ordering
  const readyWaves = waves.filter(w => w.ship_run?.status === 'ready_to_ship');
  const activeWaves = waves.filter(w => w.ship_run?.status && ['queued', 'running'].includes(w.ship_run.status));
  const failedWaves = waves.filter(w => w.ship_run?.status && ['compose_failed', 'failed'].includes(w.ship_run.status));
  const shippedWaves = waves.filter(w => w.ship_run?.status && ['shipped', 'done'].includes(w.ship_run.status));
  const pendingWaves = waves.filter(w => !w.ship_run);

  const renderSection = (title: string, items: WaveSummary[]) => {
    if (items.length === 0) return null;
    return (
      <Box sx={{ mb: 3 }}>
        <Typography variant="overline" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
          {title}
        </Typography>
        <Stack spacing={1}>
          {items.map(w => (
            <WaveCard
              key={w.wave_num}
              wave={w}
              selected={selectedWave === w.wave_num}
              onSelect={n => setSelectedWave(prev => prev === n ? null : n)}
            />
          ))}
        </Stack>
      </Box>
    );
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 1200, mx: 'auto', width: '100%' }}>
      {/* Header */}
      <Paper sx={{ p: { xs: 2, md: 3 }, mb: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
        <Stack direction={{ xs: 'column', md: 'row' }} alignItems={{ xs: 'flex-start', md: 'center' }} justifyContent="space-between" spacing={2}>
          <Box>
            <Typography variant="h4">Ship Room</Typography>
            {project && (
              <Typography color="text.secondary" sx={{ mt: 0.5 }}>{project.name}</Typography>
            )}
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mt: 1 }}>
              <Chip label={`${readyWaves.length} ready to ship`} color="warning" size="small" variant="outlined" />
              <Chip label={`${activeWaves.length} composing`} color="info" size="small" variant="outlined" />
              <Chip label={`${failedWaves.length} failed`} color="error" size="small" variant="outlined" />
              <Chip label={`${pendingWaves.length} pending`} size="small" variant="outlined" />
              <Chip label={`${shippedWaves.length} shipped`} color="success" size="small" variant="outlined" />
            </Stack>
          </Box>
          <Stack direction="row" spacing={2} alignItems="center">
            {frontier?.shipped_frontier ? (
              <Box sx={{ textAlign: 'right' }}>
                <Typography variant="caption" color="text.secondary">Current shipped frontier</Typography>
                <Typography variant="caption" sx={{ display: 'block', fontFamily: 'monospace', color: 'success.main' }}>
                  {frontier.shipped_frontier.slice(0, 12)}
                </Typography>
              </Box>
            ) : (
              <Typography variant="caption" color="warning.main">frontier not set</Typography>
            )}
            <Tooltip title="Refresh">
              <IconButton onClick={loadWaves}><RefreshIcon /></IconButton>
            </Tooltip>
          </Stack>
        </Stack>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {waves.length === 0 && !error && (
        <Paper sx={{ p: 4, textAlign: 'center', border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
          <Typography color="text.secondary">
            No waves yet. Complete some tickets to see waves here.
          </Typography>
        </Paper>
      )}

      {/* Main layout: wave list + detail panel */}
      <Box sx={{ display: 'flex', gap: 3, alignItems: 'flex-start' }}>
        {/* Left: wave list */}
        {waves.length > 0 && (
          <Box sx={{ flex: selectedWave !== null ? '0 0 340px' : 1, minWidth: 0 }}>
            {renderSection('Ready to ship', readyWaves)}
            {renderSection('Composing', activeWaves)}
            {renderSection('Failed', failedWaves)}
            {renderSection('Pending', pendingWaves)}
            {renderSection('Shipped', shippedWaves)}
          </Box>
        )}

        {/* Right: wave detail */}
        {selectedWave !== null && projectId && (
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Paper sx={{ p: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
              <WaveDetailPanel
                projectId={projectId}
                waveNum={selectedWave}
                onClose={() => setSelectedWave(null)}
              />
            </Paper>
          </Box>
        )}
      </Box>
    </Box>
  );
};

export default ShipRoomPage;
