import React, { useCallback, useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import {
  getProject,
  getTicketAttempt,
  type Project,
  type TicketAttempt,
} from '../utils/api';

const ATTEMPT_COLOR: Record<string, 'default' | 'info' | 'warning' | 'success' | 'error'> = {
  proposed: 'default',
  accepted: 'success',
  rejected: 'error',
  superseded: 'default',
  shipped: 'success',
  failed: 'error',
};

const ATTEMPT_LABEL: Record<string, string> = {
  proposed: 'Proposed',
  accepted: 'Accepted',
  rejected: 'Rejected',
  superseded: 'Superseded',
  shipped: 'Shipped',
  failed: 'Failed',
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

  const load = useCallback(async () => {
    if (!projectId || !ticketId || !attemptId) return;
    setLoading(true);
    setError(null);
    try {
      const [projectData, attemptData] = await Promise.all([
        getProject(projectId),
        getTicketAttempt(projectId, ticketId, attemptId, true),
      ]);
      setProject(projectData);
      setAttempt(attemptData);
    } catch (e: any) {
      setError(e.message);
      setAttempt(null);
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
          <Stack direction="row" spacing={1} flexWrap="wrap">
            <Button component={Link} to={`/projects/${projectId}/ship`} size="small" variant="outlined">
              Ship Room
            </Button>
            <Button onClick={load} size="small" variant="text" startIcon={<RefreshIcon />}>
              Refresh
            </Button>
          </Stack>
        </Stack>
      </Paper>

      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

      {attempt && projectId && (
        <Paper sx={{ p: 2.5, border: '1px solid rgba(148,163,184,0.45)', boxShadow: 'none' }}>
          <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" sx={{ mb: 1 }}>
            <Typography variant="subtitle1" fontWeight={600}>Attempt #{attempt.attempt_num}</Typography>
            <Chip
              label={ATTEMPT_LABEL[attempt.status] ?? attempt.status}
              size="small"
              color={ATTEMPT_COLOR[attempt.status] ?? 'default'}
            />
          </Stack>

          <Stack spacing={1.25} sx={{ mb: 2 }}>
            <Box>
              <Typography variant="overline" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>
                Commit hash
              </Typography>
              <Typography variant="body2" component="code" sx={{ fontFamily: 'monospace' }}>
                {attempt.agenthub_commit_hash}
              </Typography>
            </Box>
            <Box>
              <Typography variant="overline" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>
                Base hash
              </Typography>
              <Typography variant="body2" component="code" sx={{ fontFamily: 'monospace' }}>
                {attempt.base_hash || '(not set)'}
              </Typography>
            </Box>
          </Stack>

          {attempt.summary && (
            <Box sx={{ mb: 2 }}>
              <Typography variant="overline" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>
                Summary
              </Typography>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
                {attempt.summary}
              </Typography>
            </Box>
          )}

          <Box>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
              <Typography variant="overline" color="text.secondary" sx={{ display: 'block', lineHeight: 1.2 }}>
                Test status
              </Typography>
              <Chip
                label={attempt.test_status || 'not reported'}
                size="small"
                color={attempt.test_status === 'passed' ? 'success' : attempt.test_status === 'failed' ? 'error' : 'default'}
                variant="outlined"
              />
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
                overflow: 'auto',
                minHeight: 80,
              }}
            >
              {attempt.test_output || '(no test output)'}
            </Box>
          </Box>

        </Paper>
      )}
    </Box>
  );
};

export default AttemptDetailPage;
