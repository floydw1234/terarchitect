import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import ContentCopyIcon from '@mui/icons-material/ContentCopy';
import ExpandLessIcon from '@mui/icons-material/ExpandLess';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import RefreshIcon from '@mui/icons-material/Refresh';
import EvidencePanel from '../components/EvidencePanel';
import {
  getProject,
  getTicketAttempts,
  type Project,
  type TicketAttempt,
} from '../utils/api';

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

const AttemptDetailPage: React.FC = () => {
  const { projectId, ticketId, attemptId } = useParams<{
    projectId: string;
    ticketId: string;
    attemptId: string;
  }>();
  const [project, setProject] = useState<Project | null>(null);
  const [attempt, setAttempt] = useState<TicketAttempt | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [testExpanded, setTestExpanded] = useState(false);

  const load = useCallback(async () => {
    if (!projectId || !ticketId || !attemptId) return;
    setLoading(true);
    setError(null);
    try {
      const [projectData, attempts] = await Promise.all([
        getProject(projectId),
        getTicketAttempts(projectId, ticketId, true),
      ]);
      const found = attempts.find(a => a.id === attemptId) || null;
      setProject(projectData);
      setAttempt(found);
      if (!found) setError('Attempt not found');
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [projectId, ticketId, attemptId]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '60vh' }}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 960, mx: 'auto', width: '100%' }}>
      <Paper sx={{ p: { xs: 2, md: 3 }, mb: 3, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
        <Stack direction={{ xs: 'column', sm: 'row' }} alignItems={{ xs: 'flex-start', sm: 'center' }} justifyContent="space-between" spacing={2}>
          <Box>
            <Typography variant="h4">Attempt Detail</Typography>
            {project && <Typography color="text.secondary" sx={{ mt: 0.5 }}>{project.name}</Typography>}
          </Box>
          <Stack direction="row" spacing={1}>
            <Button component={Link} to={`/projects/${projectId}/ship`} size="small" variant="outlined">
              Ship Room
            </Button>
            <Tooltip title="Refresh">
              <IconButton onClick={load}><RefreshIcon /></IconButton>
            </Tooltip>
          </Stack>
        </Stack>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {attempt && projectId && (
        <Paper sx={{ p: 2.5, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
            <Typography variant="subtitle1" fontWeight={600}>Attempt #{attempt.attempt_num}</Typography>
            <Chip label={attempt.status} size="small" color={ATTEMPT_COLOR[attempt.status] ?? 'default'} />
            <Chip label={`wave ${attempt.wave_num}`} size="small" variant="outlined" />
            {attempt.stale && <Chip label="stale" size="small" color="warning" variant="outlined" />}
          </Stack>

          <Stack spacing={0.75} sx={{ mb: 2 }}>
            {attempt.short_commit_hash && (
              <Typography variant="caption" color="text.secondary">
                commit: <code>{attempt.short_commit_hash}</code>
              </Typography>
            )}
            {attempt.base_hash && (
              <Typography variant="caption" color="text.secondary">
                base: <code>{attempt.base_hash.slice(0, 12)}</code>
              </Typography>
            )}
            {attempt.agent_id && (
              <Typography variant="caption" color="text.secondary">
                agent: <code>{attempt.agent_id}</code>
              </Typography>
            )}
          </Stack>

          {attempt.validation_error && (
            <Alert severity="error" sx={{ mb: 2 }}>{attempt.validation_error}</Alert>
          )}

          {attempt.summary && (
            <Typography variant="body2" sx={{ mb: 2, whiteSpace: 'pre-wrap' }}>
              {attempt.summary}
            </Typography>
          )}

          {attempt.test_status && (
            <Box sx={{ mb: 2 }}>
              <Stack direction="row" alignItems="center" spacing={0.5}>
                <Chip
                  label={`Tests: ${attempt.test_status}`}
                  size="small"
                  color={attempt.test_status === 'passed' ? 'success' : attempt.test_status === 'failed' ? 'error' : 'default'}
                  variant="outlined"
                />
                <IconButton size="small" onClick={() => setTestExpanded(v => !v)}>
                  {testExpanded ? <ExpandLessIcon fontSize="small" /> : <ExpandMoreIcon fontSize="small" />}
                </IconButton>
              </Stack>
              <Collapse in={testExpanded}>
                <Stack direction="row" alignItems="flex-start" spacing={0.5} sx={{ mt: 0.5 }}>
                  <Box component="pre" sx={{ flex: 1, fontSize: '0.65rem', bgcolor: 'background.default', p: 1, borderRadius: 1, maxHeight: 320, overflow: 'auto' }}>
                    {attempt.test_output || '(no output)'}
                  </Box>
                  <Tooltip title="Copy">
                    <IconButton size="small" onClick={() => navigator.clipboard.writeText(attempt.test_output || '')}>
                      <ContentCopyIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </Stack>
              </Collapse>
            </Box>
          )}

          <EvidencePanel
            projectId={projectId}
            targetType="attempt"
            targetId={attempt.id}
            defaultCheckType="validation"
          />
        </Paper>
      )}
    </Box>
  );
};

export default AttemptDetailPage;
