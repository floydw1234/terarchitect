/**
 * Intent Inbox (plan 13.4)
 *
 * Groups intents (tickets) by computed display_state rather than kanban column.
 * Replaces Kanban as the primary planning surface once users are comfortable
 * thinking in terms of intents, attempts, and shipped state.
 *
 * Sections:
 *   Running     — agents currently executing
 *   Blocked     — waiting on a dep or external blocker
 *   Failed      — last attempt failed validation or agent crashed
 *   Attempt Ready — agent completed, attempt awaiting human review
 *   Accepted    — accepted attempt ready for composition
 *   Stale       — accepted but built before frontier advanced
 *   Composed    — in a release branch
 *   Shipped     — reached main
 *   Queued      — ready to dispatch, not yet running
 *   Archived    — intent archived
 */
import React, { useState, useEffect, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import { getProject, getTickets, type Project, type Ticket, type DisplayState } from '../utils/api';

// ---------------------------------------------------------------------------
// Section config
// ---------------------------------------------------------------------------

const SECTIONS: { state: DisplayState; label: string; color: string; description: string }[] = [
  { state: 'running',       label: 'Running',        color: '#0085FA', description: 'Agent executing now' },
  { state: 'blocked',       label: 'Blocked',        color: '#ef4444', description: 'Waiting on dep or external blocker' },
  { state: 'failed',        label: 'Failed',         color: '#ef4444', description: 'Last attempt failed validation or agent crashed' },
  { state: 'attempt_ready', label: 'Attempt Ready',  color: '#45C3F8', description: 'Agent done — attempt awaiting review' },
  { state: 'stale',         label: 'Stale',          color: '#f59e0b', description: 'Accepted but built before frontier advanced' },
  { state: 'accepted',      label: 'Accepted',       color: '#10b981', description: 'Ready for composition into a release' },
  { state: 'composed',      label: 'Composed',       color: '#0085FA', description: 'In a release branch' },
  { state: 'release_pr_open', label: 'PR Open',      color: '#0085FA', description: 'Release PR open on GitHub' },
  { state: 'shipped',       label: 'Shipped',        color: '#10b981', description: 'Reached main / shipped frontier' },
  { state: 'queued',        label: 'Queued',         color: '#6b7280', description: 'Ready to dispatch' },
];

// ---------------------------------------------------------------------------
// Intent card
// ---------------------------------------------------------------------------

function IntentCard({ ticket, projectId }: { ticket: Ticket; projectId: string }) {
  const la = ticket.latest_attempt;
  const aa = ticket.accepted_attempt;

  return (
    <Box
      sx={{
        p: 1.5,
        borderRadius: 1,
        bgcolor: 'background.default',
        border: '1px solid',
        borderColor: 'divider',
        transition: 'all 0.15s ease',
        '&:hover': {
          borderColor: 'secondary.main',
          bgcolor: 'info.light',
        },
      }}
    >
      <Stack direction="row" spacing={1} alignItems="flex-start" justifyContent="space-between">
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="body2" fontWeight={600} noWrap>
            {ticket.title}
          </Typography>
          {ticket.rationale && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
              {ticket.rationale.slice(0, 100)}{ticket.rationale.length > 100 ? '…' : ''}
            </Typography>
          )}
          <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }} flexWrap="wrap">
            <Chip label={ticket.intent_status} size="small" variant="outlined" sx={{ fontSize: '0.6rem' }} />
            <Chip label={`p:${ticket.priority}`} size="small" variant="outlined" sx={{ fontSize: '0.6rem' }} />
            {(aa || la) && (
              <Chip
                label={(aa || la)!.short_commit_hash || '?'}
                size="small"
                sx={{ fontSize: '0.6rem', fontFamily: 'monospace' }}
              />
            )}
            {la?.stale && (
              <Chip label="stale" size="small" color="warning" variant="outlined" sx={{ fontSize: '0.6rem' }} />
            )}
          </Stack>
        </Box>
        <Button
          component={Link}
          to={`/projects/${projectId}/kanban`}
          size="small"
          sx={{ flexShrink: 0, fontSize: '0.65rem' }}
        >
          Open
        </Button>
      </Stack>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

function InboxSection({
  config,
  tickets,
  projectId,
}: {
  config: typeof SECTIONS[0];
  tickets: Ticket[];
  projectId: string;
}) {
  if (tickets.length === 0) return null;

  return (
    <Box sx={{ mb: 3 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
        <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: config.color, flexShrink: 0 }} />
        <Typography variant="subtitle2">{config.label}</Typography>
        <Chip label={tickets.length} size="small" sx={{ fontSize: '0.65rem' }} />
        <Typography variant="caption" color="text.secondary">{config.description}</Typography>
      </Stack>
      <Stack spacing={0.75}>
        {tickets.map(t => (
          <IntentCard key={t.id} ticket={t} projectId={projectId} />
        ))}
      </Stack>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const IntentInboxPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    try {
      const [proj, tix] = await Promise.all([getProject(projectId), getTickets(projectId)]);
      setProject(proj);
      // Exclude draft and archived from main view by default
      setTickets(tix.filter(t => !['archived', 'draft'].includes(t.display_state || '')));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => { load(); }, [load]);

  if (loading) return (
    <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
      <CircularProgress />
    </Box>
  );

  // Group tickets by display_state
  const byState: Record<string, Ticket[]> = {};
  for (const t of tickets) {
    const s = t.display_state || 'queued';
    byState[s] = byState[s] || [];
    byState[s].push(t);
  }

  const total = tickets.length;
  const shipped = (byState['shipped'] || []).length;
  const active = (byState['running'] || []).length + (byState['accepted'] || []).length +
    (byState['attempt_ready'] || []).length + (byState['composed'] || []).length;

  return (
    <Box sx={{ maxWidth: 900, mx: 'auto', width: '100%' }}>
      {/* Header */}
      <Paper sx={{ p: { xs: 2, md: 3 }, mb: 3, border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Box>
            <Typography variant="h4">Intent Inbox</Typography>
            {project && <Typography color="text.secondary" sx={{ mt: 0.5 }}>{project.name}</Typography>}
            <Typography variant="caption" color="text.secondary">
              {total} intent{total !== 1 ? 's' : ''} · {active} active · {shipped} shipped
            </Typography>
          </Box>
          <Stack direction="row" spacing={1} alignItems="center">
            <Button component={Link} to={`/projects/${projectId}/kanban`} size="small" variant="outlined">
              Kanban
            </Button>
            <Tooltip title="Refresh">
              <IconButton onClick={load}><RefreshIcon /></IconButton>
            </Tooltip>
          </Stack>
        </Stack>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {tickets.length === 0 && (
        <Paper sx={{ p: 4, textAlign: 'center', border: '1px solid', borderColor: 'divider', boxShadow: 'none' }}>
          <Typography color="text.secondary">No active intents.</Typography>
        </Paper>
      )}

      {/* Sections in priority order */}
      {SECTIONS.map(s => (
        <InboxSection
          key={s.state}
          config={s}
          tickets={byState[s.state] || []}
          projectId={projectId!}
        />
      ))}
    </Box>
  );
};

export default IntentInboxPage;
