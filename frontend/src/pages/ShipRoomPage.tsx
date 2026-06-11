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
  composeShipCandidate,
  composeWave,
  getProject,
  getShipCandidateDetail,
  getShipCandidates,
  getShipWaveDetail,
  getShipWaves,
  getTicketAttempts,
  rejectAttempt,
  rerunTicketFromCurrentFrontier,
  sendCandidateFeedback,
  shipCandidate,
  shipWave,
  type Project,
  type ProjectFrontier,
  type PromotionCandidateDetail,
  type ShipRun,
  type TicketAttempt,
  type WaveDetail,
  type WaveSummary,
} from '../utils/api';
import { LineageField, formatLineageId } from '../components/LineageField';

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
  blocked: 'error',
  draft: 'default',
  composed: 'info',
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
  blocked: 'Blocked',
  draft: 'Draft',
  composed: 'Composed',
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

type ShipRoomTicket = {
  id: string;
  title: string;
};

type CandidateViewModel = {
  id: string;
  candidateId: string | null;
  label: string;
  status: string;
  baseRootHash: string | null;
  tickets: ShipRoomTicket[];
  acceptedAttempts: TicketAttempt[];
  shipRun: ShipRun | null;
  validationErrors: string[];
  canCompose: boolean;
  canShip: boolean;
  staleCount: number;
  feedbackSupported: boolean;
  legacyWaveNum: number | null;
  source: 'candidate' | 'wave';
};

function formatShortHash(value: string | null | undefined, width = 12) {
  return value ? value.slice(0, width) : '(not set)';
}

function getAttemptReviewLockReason(attempt: TicketAttempt, shipRunStatus: string | null | undefined) {
  if (shipRunStatus && !['compose_failed', 'failed'].includes(shipRunStatus)) {
    return `This candidate is ${STATUS_LABEL[shipRunStatus] ?? shipRunStatus} and attempt review is locked.`;
  }
  if (!REVIEWABLE_ATTEMPT_STATUSES.has(attempt.status)) {
    return 'Only proposed or validating attempts can be reviewed here.';
  }
  return null;
}

function formatCandidateLabel(candidateId: string, legacyWaveNum: number | null) {
  const readableId = candidateId.replace(/^candidate-/, '');
  const shortId = readableId.slice(0, 8) || candidateId.slice(0, 8);
  return legacyWaveNum !== null ? `Candidate ${shortId}` : `Candidate ${shortId}`;
}

function normalizeCandidateDetail(detail: PromotionCandidateDetail): CandidateViewModel {
  const shipRun = detail.latest_ship_run ?? null;
  const validationErrors = detail.validation_errors ?? [];
  const tickets = (detail.membership?.tickets ?? []).map(ticket => ({
    id: ticket.id,
    title: ticket.title,
  }));
  const acceptedAttempts = detail.membership?.attempts ?? [];
  const legacyWaveNum = detail.membership?.legacy_wave_num ?? null;
  return {
    id: detail.id,
    candidateId: detail.id,
    label: formatCandidateLabel(detail.id, legacyWaveNum),
    status: detail.status,
    baseRootHash: detail.base_root_hash,
    tickets,
    acceptedAttempts,
    shipRun,
    validationErrors,
    canCompose: validationErrors.length === 0 && (!shipRun || ['compose_failed', 'failed'].includes(shipRun.status)),
    canShip: shipRun?.status === 'ready_to_ship',
    staleCount: acceptedAttempts.filter(attempt => attempt.stale).length,
    feedbackSupported: legacyWaveNum !== null,
    legacyWaveNum,
    source: 'candidate',
  };
}

function normalizeLegacyWaveDetail(detail: WaveDetail): CandidateViewModel {
  return {
    id: `legacy-wave-${detail.wave_num}`,
    candidateId: null,
    label: `Candidate set ${detail.wave_num}`,
    status: detail.ship_run?.status ?? (detail.can_compose ? 'draft' : detail.all_done ? 'blocked' : 'queued'),
    baseRootHash: detail.shipped_frontier,
    tickets: detail.tickets.map(ticket => ({ id: ticket.id, title: ticket.title })),
    acceptedAttempts: detail.accepted_attempts,
    shipRun: detail.ship_run,
    validationErrors: detail.validation?.compose ?? [],
    canCompose: detail.can_compose,
    canShip: detail.can_ship ?? (detail.ship_run?.status === 'ready_to_ship'),
    staleCount: detail.stale_count,
    feedbackSupported: true,
    legacyWaveNum: detail.wave_num,
    source: 'wave',
  };
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

  const handleAttemptAction = async (attempt: TicketAttempt, action: 'accept' | 'reject' | 'rerun') => {
    if (action === 'rerun') {
      setBusyAttemptId(attempt.id);
      setMessage(null);
      try {
        await rerunTicketFromCurrentFrontier(projectId, attempt.ticket_id);
        setMessage(`Requeued ticket from current frontier for attempt #${attempt.attempt_num}.`);
        await onActionComplete();
      } catch (e: any) {
        setMessage(e.message);
      } finally {
        setBusyAttemptId(null);
      }
      return;
    }

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
                  {attempt.stale && <Chip label="stale" size="small" color="warning" variant="outlined" />}
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

                <Stack spacing={0.25} sx={{ mt: 0.75 }}>
                  <LineageField label="leaf" value={attempt.agenthub_commit_hash} stopPropagation />
                  <LineageField label="base" value={attempt.base_leaf_id ?? attempt.base_hash} stopPropagation />
                  <LineageField label="parent" value={attempt.parent_leaf_id ?? attempt.base_hash} stopPropagation />
                </Stack>

                {attempt.summary && (
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.75 }}>
                    {attempt.summary.slice(0, 180)}{attempt.summary.length > 180 ? '…' : ''}
                  </Typography>
                )}
                {attempt.stale_reason && (
                  <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 0.75 }}>
                    {attempt.stale_reason}
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
                  {attempt.stale && (
                    <Button
                      size="small"
                      variant="outlined"
                      color="warning"
                      onClick={() => handleAttemptAction(attempt, 'rerun')}
                      disabled={busy}
                    >
                      Rerun from frontier
                    </Button>
                  )}
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

function CandidateDetailPanel({
  projectId,
  candidate,
  onClose,
  onRefresh,
}: {
  projectId: string;
  candidate: CandidateViewModel;
  onClose: () => void;
  onRefresh: () => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  const [composing, setComposing] = useState(false);
  const [shipping, setShipping] = useState(false);
  const [feedback, setFeedback] = useState('');
  const [feedbackSending, setFeedbackSending] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [ticketAttempts, setTicketAttempts] = useState<Record<string, TicketAttempt[]>>({});
  const [attemptsLoading, setAttemptsLoading] = useState(true);

  const loadTicketAttempts = useCallback(async () => {
    setAttemptsLoading(true);
    try {
      const attemptsByTicket = await Promise.all(
        candidate.tickets.map(async ticket => {
          const attempts = await getTicketAttempts(projectId, ticket.id, true).catch(() => []);
          return [ticket.id, attempts] as const;
        }),
      );
      setTicketAttempts(Object.fromEntries(attemptsByTicket));
    } finally {
      setAttemptsLoading(false);
    }
  }, [candidate.tickets, projectId]);

  useEffect(() => {
    loadTicketAttempts();
  }, [loadTicketAttempts]);

  const handleRefresh = async () => {
    setError(null);
    await onRefresh();
  };

  const composeLabel =
    candidate.shipRun && ['compose_failed', 'failed'].includes(candidate.shipRun.status) ? 'Retry compose' : 'Compose candidate';

  const handleCompose = async () => {
    setComposing(true);
    setError(null);
    try {
      if (candidate.source === 'candidate' && candidate.candidateId) {
        await composeShipCandidate(projectId, candidate.candidateId);
      } else if (candidate.legacyWaveNum !== null) {
        await composeWave(projectId, candidate.legacyWaveNum);
      }
      await onRefresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setComposing(false);
    }
  };

  const handleShip = async () => {
    setShipping(true);
    setError(null);
    try {
      if (candidate.source === 'candidate' && candidate.candidateId) {
        await shipCandidate(projectId, candidate.candidateId);
      } else if (candidate.legacyWaveNum !== null) {
        await shipWave(projectId, candidate.legacyWaveNum);
      }
      await onRefresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setShipping(false);
    }
  };

  const handleFeedback = async () => {
    if (!feedback.trim()) return;
    setFeedbackSending(true);
    setError(null);
    try {
      if (candidate.source === 'candidate' && candidate.candidateId) {
        await sendCandidateFeedback(projectId, candidate.candidateId, feedback.trim());
      } else {
        throw new Error('Feedback is not supported for this candidate on the current frontend path.');
      }
      setFeedback('');
      setFeedbackSent(true);
      setTimeout(() => setFeedbackSent(false), 3000);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setFeedbackSending(false);
    }
  };

  return (
    <Box>
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} justifyContent="space-between" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="h6">{candidate.label}</Typography>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mt: 1 }}>
            <Chip label={`${candidate.acceptedAttempts.length} accepted`} size="small" color="success" variant="outlined" />
            <Chip label={`${candidate.tickets.length} tickets`} size="small" variant="outlined" />
            <Chip label={`Frontier ${formatShortHash(candidate.baseRootHash)}`} size="small" variant="outlined" />
          </Stack>
        </Box>
        <Stack direction="row" spacing={1} flexWrap="wrap">
          <Button size="small" onClick={handleRefresh} startIcon={<RefreshIcon fontSize="small" />}>
            Refresh
          </Button>
          <Button size="small" onClick={onClose}>
            Close
          </Button>
        </Stack>
      </Stack>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {candidate.staleCount > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {candidate.staleCount} attempt{candidate.staleCount !== 1 ? 's were' : ' was'} built before the current frontier.
        </Alert>
      )}

      {candidate.validationErrors.length > 0 && (
        <Alert severity="warning" sx={{ mb: 2 }}>
          {candidate.validationErrors[0]}
        </Alert>
      )}

      <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mb: 2 }}>
        {candidate.canCompose && (
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
        {candidate.canShip && (
          <Button
            variant="contained"
            color="success"
            size="small"
            onClick={handleShip}
            disabled={shipping}
            startIcon={shipping ? <CircularProgress size={12} color="inherit" /> : undefined}
          >
            {shipping ? 'Shipping…' : 'Ship candidate'}
          </Button>
        )}
      </Stack>

      {candidate.shipRun && (
        <Box sx={{ mb: 2 }}>
          <ShipRunPanel run={candidate.shipRun} title="Ship run" />
        </Box>
      )}

      <Box sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>
          Accepted attempts ({candidate.acceptedAttempts.length})
        </Typography>
        {candidate.acceptedAttempts.length === 0 ? (
          <Typography variant="body2" color="text.secondary">
            No accepted attempts yet.
          </Typography>
        ) : (
          <Stack spacing={1}>
            {candidate.acceptedAttempts.map(attempt => (
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
                  {attempt.stale && <Chip label="stale" size="small" color="warning" variant="outlined" />}
                  <Button
                    component={Link}
                    to={`/projects/${projectId}/tickets/${attempt.ticket_id}/attempts/${attempt.id}`}
                    size="small"
                  >
                    Inspect
                  </Button>
                </Stack>
                <Stack spacing={0.25} sx={{ mt: 0.75 }}>
                  <LineageField label="leaf" value={attempt.agenthub_commit_hash} stopPropagation />
                  <LineageField label="base" value={attempt.base_leaf_id ?? attempt.base_hash} stopPropagation />
                  <LineageField label="parent" value={attempt.parent_leaf_id ?? attempt.base_hash} stopPropagation />
                </Stack>
                {candidate.shipRun?.status === 'shipped' && (
                  <Typography variant="caption" color="success.main" sx={{ display: 'block', mt: 0.75 }}>
                    Selected attempt from the shipped candidate.
                  </Typography>
                )}
                {attempt.stale_reason && (
                  <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 0.75 }}>
                    {attempt.stale_reason}
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
        {attemptsLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', p: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : (
          <Stack spacing={1.25}>
            {candidate.tickets.map(ticket => {
              const attempts = ticketAttempts[ticket.id] ?? [];
              const latestAttempt = attempts[0] ?? null;
              return (
                <Paper key={ticket.id} data-testid={`ticket-card-${ticket.id}`} sx={{ p: 1.5, bgcolor: 'background.default' }}>
                  <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
                    <Typography variant="body2" fontWeight={600}>
                      {ticket.title}
                    </Typography>
                    {latestAttempt && (
                      <Chip
                        label={`${ATTEMPT_LABEL[latestAttempt.status] ?? latestAttempt.status}${latestAttempt.short_commit_hash ? ` · ${latestAttempt.short_commit_hash}` : ''}`}
                        size="small"
                        color={ATTEMPT_COLOR[latestAttempt.status] ?? 'default'}
                      />
                    )}
                  </Stack>
                  <AttemptReviewCard
                    projectId={projectId}
                    attempts={attempts}
                    shipRunStatus={candidate.shipRun?.status}
                    onActionComplete={onRefresh}
                  />
                </Paper>
              );
            })}
          </Stack>
        )}
      </Box>

      {candidate.feedbackSupported ? (
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
              disabled={feedbackSending || !feedback.trim() || candidate.source !== 'candidate'}
              sx={{ minWidth: 96, alignSelf: 'stretch' }}
            >
              {feedbackSending ? <CircularProgress size={14} /> : feedbackSent ? 'Sent!' : 'Send'}
            </Button>
          </Stack>
        </Box>
      ) : null}
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

function CandidateCard({
  candidate,
  selected,
  onSelect,
}: {
  candidate: CandidateViewModel;
  selected: boolean;
  onSelect: (candidateId: string) => void;
}) {
  const run = candidate.shipRun;

  return (
    <Paper
      onClick={() => onSelect(candidate.id)}
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
            {candidate.label}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {candidate.acceptedAttempts.length} accepted across {candidate.tickets.length} ticket{candidate.tickets.length !== 1 ? 's' : ''}
          </Typography>
        </Box>
        {run ? (
          <Chip
            label={STATUS_LABEL[run.status] ?? run.status}
            size="small"
            color={STATUS_COLOR[run.status] ?? 'default'}
          />
        ) : (
          <Chip
            label={candidate.canCompose ? 'Ready for compose' : candidate.validationErrors.length ? 'Blocked' : 'Waiting on attempts'}
            size="small"
            variant="outlined"
          />
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
  const [candidates, setCandidates] = useState<CandidateViewModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const projectData = await getProject(projectId);
      setProject(projectData);
      setFrontier({
        shipped_frontier: projectData.shipped_frontier ?? null,
        shipped_frontier_updated_at: projectData.shipped_frontier_updated_at ?? null,
        frontier_warning: projectData.frontier_warning ?? null,
      });

      let normalizedCandidates: CandidateViewModel[] = [];
      try {
        const candidateList = await getShipCandidates(projectId);
        if (candidateList.length > 0) {
          const candidateDetails = await Promise.all(candidateList.map(candidate => getShipCandidateDetail(projectId, candidate.id)));
          normalizedCandidates = candidateDetails.map(normalizeCandidateDetail);
        }
      } catch (candidateError: any) {
        if (!String(candidateError?.message || '').includes('404')) {
          throw candidateError;
        }
      }

      if (normalizedCandidates.length === 0) {
        const waveData = await getShipWaves(projectId);
        const waveDetails = await Promise.all(
          waveData.map(async (wave: WaveSummary) => getShipWaveDetail(projectId, wave.wave_num)),
        );
        normalizedCandidates = waveDetails.map(normalizeLegacyWaveDetail);
      }

      setCandidates(normalizedCandidates);
      setSelectedCandidateId(current => (
        current && normalizedCandidates.some(candidate => candidate.id === current)
          ? current
          : normalizedCandidates[0]?.id ?? null
      ));
    } catch (e: any) {
      setError(e.message);
      setCandidates([]);
      setSelectedCandidateId(null);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const selectedCandidate = useMemo(
    () => candidates.find(candidate => candidate.id === selectedCandidateId) ?? null,
    [candidates, selectedCandidateId],
  );

  const acceptedCount = useMemo(
    () => candidates.reduce((sum, candidate) => sum + candidate.acceptedAttempts.length, 0),
    [candidates],
  );

  const activeShipRun = useMemo(
    () =>
      candidates
        .map(candidate => candidate.shipRun)
        .filter((run): run is ShipRun => !!run && !['shipped', 'done'].includes(run.status))[0] ?? null,
    [candidates],
  );

  const latestShipRun = useMemo(
    () => candidates.map(candidate => candidate.shipRun).find((run): run is ShipRun => !!run) ?? null,
    [candidates],
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
              Review promotion candidates, accept or reject attempts, compose candidate-backed ship runs, inspect failures, ship the ready run, or send feedback when the backend supports it.
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
          label="Accepted AgentHub frontier"
          value={formatLineageId(project?.accepted_frontier_id ?? frontier?.shipped_frontier)}
          detail={
            project?.shipped_frontier && project?.accepted_frontier_id && project.shipped_frontier !== project.accepted_frontier_id
              ? `Shipped ${formatLineageId(project.shipped_frontier)}`
              : frontier?.frontier_warning ?? undefined
          }
        />
        <SummaryCard
          label="Promotion candidates"
          value={`${candidates.length}`}
          detail={`${acceptedCount} accepted attempt${acceptedCount !== 1 ? 's' : ''} across visible candidates`}
        />
        <SummaryCard
          label={activeShipRun ? 'Active ship run' : 'Latest ship run'}
          value={activeShipRun ? activeShipRun.id.slice(0, 8) : latestShipRun ? latestShipRun.id.slice(0, 8) : 'None'}
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

      {candidates.length === 0 && !error ? (
        <Paper sx={{ p: 4, textAlign: 'center', border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
          <Typography color="text.secondary">
            No promotion candidates yet. Accept some ticket attempts to build a candidate set.
          </Typography>
        </Paper>
      ) : (
        <Box sx={{ display: 'grid', gap: 3, gridTemplateColumns: { xs: '1fr', lg: selectedCandidate ? '360px minmax(0, 1fr)' : '1fr' } }}>
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>
              Promotion candidates
            </Typography>
            <Stack spacing={1}>
              {candidates.map(candidate => (
                <CandidateCard
                  key={candidate.id}
                  candidate={candidate}
                  selected={selectedCandidateId === candidate.id}
                  onSelect={candidateId => setSelectedCandidateId(current => (current === candidateId ? null : candidateId))}
                />
              ))}
            </Stack>
          </Box>

          {selectedCandidate && projectId && (
            <Paper sx={{ p: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
              <CandidateDetailPanel
                projectId={projectId}
                candidate={selectedCandidate}
                onClose={() => setSelectedCandidateId(null)}
                onRefresh={load}
              />
            </Paper>
          )}
        </Box>
      )}
    </Box>
  );
};

export default ShipRoomPage;
