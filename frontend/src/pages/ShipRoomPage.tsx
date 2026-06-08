import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import {
  acceptAttempt,
  composeWave,
  getProject,
  getShipWaveDetail,
  getShipWaves,
  getTicketAttempts,
  rejectAttempt,
  sendWaveFeedback,
  shipWave,
  type Project,
  type ProjectFrontier,
  type ShipRun,
  type TicketAttempt,
  type WaveDetail,
  type WaveSummary,
} from '../utils/api';

const STATUS_COLOR: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  queued: 'info',
  running: 'info',
  composing: 'info',
  compose_failed: 'error',
  failed: 'error',
  ready_to_ship: 'warning',
  shipping: 'warning',
  shipped: 'success',
  done: 'success',
};

const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued',
  running: 'Composing',
  composing: 'Composing',
  compose_failed: 'Compose Failed',
  failed: 'Failed',
  ready_to_ship: 'Ready to Ship',
  shipping: 'Shipping',
  shipped: 'Shipped',
  done: 'Done',
};

const ATTEMPT_COLOR: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  proposed: 'default',
  validating: 'info',
  accepted: 'success',
  rejected: 'error',
  superseded: 'default',
  failed: 'error',
  shipped: 'success',
};

const ATTEMPT_LABEL: Record<string, string> = {
  proposed: 'Proposed',
  validating: 'Validating',
  accepted: 'Accepted',
  rejected: 'Rejected',
  superseded: 'Superseded',
  failed: 'Failed',
  shipped: 'Shipped',
};

const REVIEWABLE_ATTEMPT_STATUSES = new Set(['proposed', 'validating']);

function formatShortHash(value: string | null | undefined, width = 12) {
  return value ? value.slice(0, width) : '(not set)';
}

function getAttemptReviewLockReason(attempt: TicketAttempt, shipRunStatus: string | null | undefined) {
  if (shipRunStatus && !['compose_failed', 'failed'].includes(shipRunStatus)) {
    return `This wave is ${STATUS_LABEL[shipRunStatus] ?? shipRunStatus} and attempt review is locked.`;
  }
  if (!REVIEWABLE_ATTEMPT_STATUSES.has(attempt.status)) {
    return 'Only proposed or validating attempts can be reviewed here.';
  }
  return null;
}

function ShipRunPanel({ run, title }: { run: ShipRun; title: string }) {
  const showDiagnostics = !!(run.error || run.test_output || run.test_status);

  return (
    <Paper sx={{ p: 2, border: '1px solid rgba(148,163,184,0.2)', boxShadow: 'none' }}>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} justifyContent="space-between">
        <Box>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
            <Typography variant="subtitle2" fontWeight={600}>
              {title}
            </Typography>
            <Chip
              label={STATUS_LABEL[run.status] ?? run.status}
              color={STATUS_COLOR[run.status] ?? 'default'}
              size="small"
            />
          </Stack>
          <Stack spacing={0.5} sx={{ mt: 1 }}>
            <Typography variant="caption" color="text.secondary">
              Base: <code>{formatShortHash(run.base_main_hash)}</code>
            </Typography>
            {run.composed_commit_hash && (
              <Typography variant="caption" color="text.secondary">
                Composed: <code>{formatShortHash(run.composed_commit_hash)}</code>
              </Typography>
            )}
            {run.shipped_commit_hash && (
              <Typography variant="caption" color="text.secondary">
                Shipped: <code>{formatShortHash(run.shipped_commit_hash)}</code>
              </Typography>
            )}
          </Stack>
        </Box>
        <Stack spacing={1} alignItems={{ xs: 'flex-start', sm: 'flex-end' }}>
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
          {run.release_branch && (
            <Typography variant="caption" color="text.secondary">
              Branch: <code>{run.release_branch}</code>
            </Typography>
          )}
        </Stack>
      </Stack>

      {run.summary && (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
          {run.summary}
        </Typography>
      )}

      {showDiagnostics && (
        <Box sx={{ mt: 1.5 }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
            {run.test_status && (
              <Chip
                label={`Tests: ${run.test_status}`}
                size="small"
                variant="outlined"
                color={run.test_status === 'passed' ? 'success' : run.test_status === 'failed' ? 'error' : 'default'}
              />
            )}
            {run.error && <Chip label="Failure output" size="small" variant="outlined" color="error" />}
          </Stack>
          <Box
            component="pre"
            sx={{
              m: 0,
              p: 1.5,
              borderRadius: 1,
              bgcolor: 'background.default',
              border: '1px solid rgba(148,163,184,0.18)',
              fontSize: '0.75rem',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              overflow: 'auto',
              maxHeight: 280,
            }}
          >
            {run.error || run.test_output || (run.test_status === 'skipped' ? '(no test command configured)' : '(no output)')}
          </Box>
        </Box>
      )}
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
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
        <Typography variant="body2" fontWeight={600}>
          Review attempts
        </Typography>
        <Chip label={`${attempts.length} visible`} size="small" variant="outlined" />
      </Stack>

      {message && (
        <Alert severity={message.includes('locked') ? 'warning' : 'info'} sx={{ mb: 1 }}>
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
                    label={ATTEMPT_LABEL[attempt.status] ?? attempt.status}
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
                    <Typography variant="caption" color="text.secondary">
                      base <code>{formatShortHash(attempt.base_hash)}</code>
                    </Typography>
                  )}
                  {attempt.test_status && (
                    <Chip
                      label={`tests: ${attempt.test_status}`}
                      size="small"
                      variant="outlined"
                      color={attempt.test_status === 'passed' ? 'success' : attempt.test_status === 'failed' ? 'error' : 'default'}
                    />
                  )}
                  <Button
                    component={Link}
                    to={`/projects/${projectId}/tickets/${attempt.ticket_id}/attempts/${attempt.id}`}
                    size="small"
                  >
                    Inspect
                  </Button>
                </Stack>

                {attempt.summary && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.75 }}>
                    {attempt.summary.slice(0, 180)}{attempt.summary.length > 180 ? '…' : ''}
                  </Typography>
                )}

                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mt: 1 }}>
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
  const [ticketAttempts, setTicketAttempts] = useState<Record<string, TicketAttempt[]>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const waveDetail = await getShipWaveDetail(projectId, waveNum);
      setDetail(waveDetail);
      const attemptsByTicket = await Promise.all(
        waveDetail.tickets.map(async ticket => {
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

  useEffect(() => {
    load();
  }, [load]);

  const shipRun = detail?.ship_run ?? null;
  const canShip = shipRun?.status === 'ready_to_ship' && !!shipRun.release_pr_number;
  const composeLabel =
    shipRun && ['compose_failed', 'failed'].includes(shipRun.status) ? 'Retry compose' : 'Compose wave';

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
    return <Alert severity="error">{error}</Alert>;
  }

  if (!detail) {
    return null;
  }

  return (
    <Box>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} justifyContent="space-between" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="h6">Wave {waveNum}</Typography>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mt: 1 }}>
            <Chip label={`${detail.accepted_attempts.length} accepted`} size="small" color="success" variant="outlined" />
            <Chip label={`${detail.tickets.length} tickets`} size="small" variant="outlined" />
            <Chip label={`Frontier ${formatShortHash(detail.shipped_frontier)}`} size="small" variant="outlined" />
          </Stack>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap">
          <Button size="small" onClick={load} startIcon={<RefreshIcon fontSize="small" />}>
            Refresh
          </Button>
          <Button size="small" onClick={onClose}>
            Close
          </Button>
        </Stack>
      </Stack>

      {detail.stale_count > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {detail.stale_count} attempt{detail.stale_count !== 1 ? 's were' : ' was'} built before the current frontier.
        </Alert>
      )}

      <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 2 }}>
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
      </Stack>

      {shipRun && (
        <Box sx={{ mb: 2 }}>
          <ShipRunPanel run={shipRun} title="Ship run" />
        </Box>
      )}

      <Box sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Accepted attempts ({detail.accepted_attempts.length})
        </Typography>
        {detail.accepted_attempts.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No accepted attempts yet.
          </Typography>
        ) : (
          <Stack spacing={1}>
            {detail.accepted_attempts.map(attempt => (
              <Paper key={attempt.id} sx={{ p: 1.5, bgcolor: 'background.default' }}>
                <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
                  <Chip
                    label={ATTEMPT_LABEL[attempt.status] ?? attempt.status}
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
                  <Button
                    component={Link}
                    to={`/projects/${projectId}/tickets/${attempt.ticket_id}/attempts/${attempt.id}`}
                    size="small"
                  >
                    Inspect
                  </Button>
                </Stack>
                {shipRun?.status === 'shipped' && (
                  <Typography variant="caption" color="success.main" sx={{ display: 'block', mt: 0.75 }}>
                    Selected attempt from the shipped wave.
                  </Typography>
                )}
                {attempt.summary && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.75 }}>
                    {attempt.summary}
                  </Typography>
                )}
              </Paper>
            ))}
          </Stack>
        )}
      </Box>

      <Box sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Review attempts by ticket
        </Typography>
        <Stack spacing={1.25}>
          {detail.tickets.map(ticket => (
            <Paper key={ticket.id} data-testid={`ticket-card-${ticket.id}`} sx={{ p: 1.5, bgcolor: 'background.default' }}>
              <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
                <Typography variant="body2" fontWeight={600}>
                  {ticket.title}
                </Typography>
                {ticket.latest_attempt && (
                  <Chip
                    label={`${ATTEMPT_LABEL[ticket.latest_attempt.status] ?? ticket.latest_attempt.status}${ticket.latest_attempt.short_commit_hash ? ` · ${ticket.latest_attempt.short_commit_hash}` : ''}`}
                    size="small"
                    color={ATTEMPT_COLOR[ticket.latest_attempt.status] ?? 'default'}
                  />
                )}
              </Stack>
              <AttemptReviewCard
                projectId={projectId}
                attempts={ticketAttempts[ticket.id] ?? []}
                shipRunStatus={shipRun?.status}
                onActionComplete={load}
              />
            </Paper>
          ))}
        </Stack>
      </Box>

      <Box>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Send feedback
        </Typography>
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems="flex-start">
          <TextField
            size="small"
            multiline
            minRows={2}
            fullWidth
            placeholder="Describe what needs to change…"
            value={feedback}
            onChange={event => setFeedback(event.target.value)}
          />
          <Button
            variant="outlined"
            size="small"
            onClick={handleFeedback}
            disabled={feedbackSending || !feedback.trim()}
            sx={{ minWidth: 96, alignSelf: 'stretch' }}
          >
            {feedbackSending ? <CircularProgress size={14} /> : feedbackSent ? 'Sent!' : 'Send'}
          </Button>
        </Stack>
      </Box>
    </Box>
  );
}

function SummaryCard({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail?: string;
}) {
  return (
    <Paper sx={{ p: 2, minWidth: 0, border: '1px solid rgba(148,163,184,0.2)', boxShadow: 'none' }}>
      <Typography variant="caption" color="text.secondary">
        {label}
      </Typography>
      <Typography variant="subtitle1" fontWeight={600} sx={{ mt: 0.5 }}>
        {value}
      </Typography>
      {detail && (
        <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
          {detail}
        </Typography>
      )}
    </Paper>
  );
}

function WaveCard({
  wave,
  selected,
  onSelect,
}: {
  wave: WaveSummary;
  selected: boolean;
  onSelect: (waveNum: number) => void;
}) {
  const run = wave.ship_run;

  return (
    <Paper
      onClick={() => onSelect(wave.wave_num)}
      sx={{
        p: 2,
        cursor: 'pointer',
        border: selected ? '2px solid' : '1px solid rgba(148,163,184,0.2)',
        borderColor: selected ? 'primary.main' : undefined,
        boxShadow: 'none',
      }}
    >
      <Stack direction="row" spacing={1} justifyContent="space-between" alignItems="flex-start">
        <Box>
          <Typography variant="subtitle2" fontWeight={600}>
            Wave {wave.wave_num}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {wave.accepted_count} accepted of {wave.ticket_count} ticket{wave.ticket_count !== 1 ? 's' : ''}
          </Typography>
        </Box>
        {run ? (
          <Chip
            label={STATUS_LABEL[run.status] ?? run.status}
            size="small"
            color={STATUS_COLOR[run.status] ?? 'default'}
          />
        ) : (
          <Chip label={wave.all_done ? 'Ready for review' : 'Waiting on attempts'} size="small" variant="outlined" />
        )}
      </Stack>
      {run?.release_pr_url && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          Release PR #{run.release_pr_number}
        </Typography>
      )}
      {run?.error && (
        <Typography variant="caption" color="error.main" sx={{ display: 'block', mt: 1 }}>
          {run.error}
        </Typography>
      )}
      {run?.shipped_commit_hash && (
        <Typography variant="caption" color="success.main" sx={{ display: 'block', mt: 1 }}>
          Shipped · {formatShortHash(run.shipped_commit_hash, 10)}
        </Typography>
      )}
    </Paper>
  );
}

const ShipRoomPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [frontier, setFrontier] = useState<ProjectFrontier | null>(null);
  const [waves, setWaves] = useState<WaveSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedWave, setSelectedWave] = useState<number | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const [projectData, waveData] = await Promise.all([getProject(projectId), getShipWaves(projectId)]);
      setProject(projectData);
      setWaves(waveData.sort((a, b) => b.wave_num - a.wave_num));
      setFrontier({
        shipped_frontier: projectData.shipped_frontier ?? null,
        shipped_frontier_updated_at: projectData.shipped_frontier_updated_at ?? null,
        frontier_warning: projectData.frontier_warning ?? null,
      });
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const acceptedCount = useMemo(
    () => waves.reduce((sum, wave) => sum + wave.accepted_count, 0),
    [waves],
  );

  const activeShipRun = useMemo(
    () =>
      waves
        .map(wave => wave.ship_run)
        .filter((run): run is ShipRun => !!run && !['shipped', 'done'].includes(run.status))
        .sort((a, b) => b.wave_num - a.wave_num)[0] ?? null,
    [waves],
  );

  const latestShipRun = useMemo(
    () =>
      waves
        .map(wave => wave.ship_run)
        .filter((run): run is ShipRun => !!run)
        .sort((a, b) => b.wave_num - a.wave_num)[0] ?? null,
    [waves],
  );

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 1200, mx: 'auto', width: '100%' }}>
      <Paper sx={{ p: { xs: 2, md: 3 }, mb: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} justifyContent="space-between">
          <Box>
            <Typography variant="h4">Ship Room</Typography>
            {project && (
              <Typography color="text.secondary" sx={{ mt: 0.5 }}>
                {project.name}
              </Typography>
            )}
            <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
              Review attempts, accept or reject, compose the wave, inspect failures, ship the release boundary, or send feedback.
            </Typography>
          </Box>
          <Button size="small" onClick={load} startIcon={<RefreshIcon fontSize="small" />} sx={{ alignSelf: 'flex-start' }}>
            Refresh
          </Button>
        </Stack>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: 'repeat(3, minmax(0, 1fr))' }, mb: 3 }}>
        <SummaryCard
          label="Current shipped frontier"
          value={formatShortHash(frontier?.shipped_frontier)}
          detail={frontier?.frontier_warning ?? undefined}
        />
        <SummaryCard
          label="Waves"
          value={`${waves.length}`}
          detail={`${acceptedCount} accepted attempt${acceptedCount !== 1 ? 's' : ''} across all waves`}
        />
        <SummaryCard
          label={activeShipRun ? 'Active ship run' : 'Latest ship run'}
          value={activeShipRun ? `Wave ${activeShipRun.wave_num}` : latestShipRun ? `Wave ${latestShipRun.wave_num}` : 'None'}
          detail={
            activeShipRun
              ? STATUS_LABEL[activeShipRun.status] ?? activeShipRun.status
              : latestShipRun
                ? STATUS_LABEL[latestShipRun.status] ?? latestShipRun.status
                : 'No ship run has been created yet'
          }
        />
      </Box>

      {(activeShipRun || latestShipRun) && (
        <Box sx={{ mb: 3 }}>
          <ShipRunPanel
            run={activeShipRun ?? latestShipRun!}
            title={activeShipRun ? 'Active ship run' : 'Latest ship run'}
          />
        </Box>
      )}

      {waves.length === 0 && !error ? (
        <Paper sx={{ p: 4, textAlign: 'center', border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
          <Typography color="text.secondary">
            No waves yet. Complete some tickets to see waves here.
          </Typography>
        </Paper>
      ) : (
        <Box sx={{ display: 'grid', gap: 3, gridTemplateColumns: { xs: '1fr', lg: selectedWave !== null ? '360px minmax(0, 1fr)' : '1fr' } }}>
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Waves
            </Typography>
            <Stack spacing={1}>
              {waves.map(wave => (
                <WaveCard
                  key={wave.wave_num}
                  wave={wave}
                  selected={selectedWave === wave.wave_num}
                  onSelect={waveNum => setSelectedWave(current => (current === waveNum ? null : waveNum))}
                />
              ))}
            </Stack>
          </Box>

          {selectedWave !== null && projectId && (
            <Paper sx={{ p: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
              <WaveDetailPanel
                projectId={projectId}
                waveNum={selectedWave}
                onClose={() => setSelectedWave(null)}
              />
            </Paper>
          )}
        </Box>
      )}
    </Box>
  );
};

export default ShipRoomPage;
