import React, { useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Box,
  Typography,
  Paper,
  Button,
  CircularProgress,
  List,
  ListItem,
  ListItemText,
  TextField,
  Tooltip,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ScheduleIcon from '@mui/icons-material/Schedule';
import {
  getProject,
  getReview,
  postReviewComment,
  approveReview,
  mergeReview,
  type Project,
} from '../utils/api';

function TestNamesList({ names }: { names: string[] }) {
  if (names.length === 0) return <> (test names could not be extracted)</>;
  return (
    <>
      {names.map((name, j) => (
        <Box key={j} component="span" sx={{ display: 'block', mt: 0.25 }}>
          · {name}
        </Box>
      ))}
    </>
  );
}

const ReviewDetailPage: React.FC = () => {
  const { projectId, ticketId } = useParams<{ projectId: string; ticketId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [summary, setSummary] = useState('');
  const [commits, setCommits] = useState<{ sha: string; message: string }[]>([]);
  const [comments, setComments] = useState<{ author: string; body: string; created_at: string | null }[]>([]);
  const [testFiles, setTestFiles] = useState<{ path: string; test_names: string[] }[]>([]);
  const [testsDescription, setTestsDescription] = useState<string>('');
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [prNumber, setPrNumber] = useState<number | null>(null);
  const [prState, setPrState] = useState<string>('unknown');
  const [merged, setMerged] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState('');
  const [commentSubmitting, setCommentSubmitting] = useState(false);
  const [approveMergeSubmitting, setApproveMergeSubmitting] = useState(false);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const fetchReview = useCallback(async () => {
    if (!projectId || !ticketId) return;
    setLoading(true);
    setError(null);
    try {
      const [p, r] = await Promise.all([getProject(projectId), getReview(projectId, ticketId)]);
      setProject(p);
      setSummary(r.summary);
      setCommits(r.commits);
      setComments(r.comments ?? []);
      setTestFiles(r.test_files ?? []);
      setTestsDescription(r.tests_description ?? '');
      setPrUrl(r.pr_url);
      setPrNumber(r.pr_number);
      setPrState(r.pr_state ?? 'unknown');
      setMerged(r.merged ?? false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load review');
    } finally {
      setLoading(false);
    }
  }, [projectId, ticketId]);

  React.useEffect(() => {
    fetchReview();
  }, [fetchReview]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 200 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (error) {
    return (
      <Box sx={{ maxWidth: 700, mx: 'auto', width: '100%' }}>
        <Typography color="error" sx={{ mb: 2 }}>{error}</Typography>
        <Typography component={Link} to={`/projects/${projectId}/review`} sx={{ color: 'primary.main', textDecoration: 'none' }}>
          ← Back to review list
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ maxWidth: 700, mx: 'auto', width: '100%' }}>
      <Box sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        <Typography component={Link} to={`/projects/${projectId}/review`} sx={{ color: 'text.secondary', textDecoration: 'none', fontSize: '0.9rem' }}>
          ← Review
        </Typography>
        {project && (
          <Typography variant="body2" color="text.secondary">
            {project.name}
          </Typography>
        )}
      </Box>

      <Box sx={{ mb: 2, display: 'flex', alignItems: 'center', gap: 2, flexWrap: 'wrap' }}>
        {merged ? (
          <Tooltip title="Merged">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <CheckCircleIcon color="success" fontSize="small" />
              <Typography variant="body2" color="success.main">Merged</Typography>
            </Box>
          </Tooltip>
        ) : (
          <Tooltip title="Open">
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
              <ScheduleIcon color="action" fontSize="small" />
              <Typography variant="body2" color="text.secondary">Pending</Typography>
            </Box>
          </Tooltip>
        )}
        {prUrl && (
          <Button
            variant="contained"
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
          >
            Open PR on GitHub {prNumber != null ? `#${prNumber}` : ''}
          </Button>
        )}
      </Box>

      {actionMessage && (
        <Typography variant="body2" color="success.main" sx={{ mb: 2 }}>
          {actionMessage}
        </Typography>
      )}

      {!merged && (
        <Box sx={{ mb: 3 }}>
          <Button
            variant="outlined"
            color="primary"
            size="small"
            disabled={approveMergeSubmitting}
            onClick={async () => {
              if (!projectId || !ticketId) return;
              setApproveMergeSubmitting(true);
              setActionMessage(null);
              try {
                await approveReview(projectId, ticketId);
                await mergeReview(projectId, ticketId, 'merge');
                setActionMessage('PR approved and merged.');
                fetchReview();
              } catch (e) {
                console.error(e);
              } finally {
                setApproveMergeSubmitting(false);
              }
            }}
          >
            {approveMergeSubmitting ? 'Approving and merging…' : 'Approve and merge'}
          </Button>
        </Box>
      )}

      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
        What was accomplished
      </Typography>
      <Paper
        sx={{
          p: 2,
          mb: 3,
          border: '1px solid rgba(148, 163, 184, 0.35)',
          boxShadow: 'none',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {summary || 'No summary.'}
      </Paper>

      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
        Conversation
      </Typography>
      <Paper sx={{ p: 2, mb: 3, border: '1px solid rgba(148, 163, 184, 0.35)', boxShadow: 'none' }}>
        {comments.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ mb: merged ? 0 : 2 }}>
            {merged ? 'No comments.' : 'No comments yet.'}
          </Typography>
        ) : (
          <List dense disablePadding sx={{ mb: merged ? 0 : 2 }}>
            {comments.map((c, i) => (
              <ListItem key={i} alignItems="flex-start" disablePadding sx={{ flexDirection: 'column', alignItems: 'stretch' }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 1, flexWrap: 'wrap' }}>
                  <Typography variant="caption" fontWeight={600}>
                    {c.author}
                  </Typography>
                  {c.created_at && (
                    <Typography variant="caption" color="text.secondary">
                      {new Date(c.created_at).toLocaleString()}
                    </Typography>
                  )}
                </Box>
                <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', mt: 0.5 }}>
                  {c.body || '(no body)'}
                </Typography>
                {i < comments.length - 1 && <Box sx={{ borderBottom: '1px solid rgba(148, 163, 184, 0.2)', my: 1.5 }} />}
              </ListItem>
            ))}
          </List>
        )}
        {!merged && (
          <>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
              Add a comment
            </Typography>
            <TextField
              fullWidth
              multiline
              minRows={2}
              placeholder="Write a comment on the PR..."
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              size="small"
              sx={{ mb: 1 }}
            />
            <Button
              variant="outlined"
              size="small"
              disabled={!comment.trim() || commentSubmitting}
              onClick={async () => {
                if (!projectId || !ticketId || !comment.trim()) return;
                setCommentSubmitting(true);
                setActionMessage(null);
                try {
                  await postReviewComment(projectId, ticketId, comment.trim());
                  setComment('');
                  setActionMessage('Comment posted.');
                  fetchReview();
                } catch (e) {
                  setActionMessage(null);
                  console.error(e);
                } finally {
                  setCommentSubmitting(false);
                }
              }}
            >
              {commentSubmitting ? 'Posting…' : 'Post comment'}
            </Button>
          </>
        )}
      </Paper>

      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
        Tests
      </Typography>
      <Paper sx={{ border: '1px solid rgba(148, 163, 184, 0.35)', boxShadow: 'none', mb: 3 }}>
        {testsDescription ? (
          <Box sx={{ px: 2, pt: 2, pb: testFiles.length > 0 ? 1 : 0 }}>
            <Typography variant="body2" color="text.secondary">{testsDescription}</Typography>
          </Box>
        ) : null}
        {testFiles.length === 0 ? (
          <Box sx={{ p: 2, color: 'text.secondary' }}>No test files detected in this PR.</Box>
        ) : (
          <List dense disablePadding>
            {testFiles.map((tf, i) => (
              <ListItem key={i} divider={i < testFiles.length - 1}>
                <ListItemText
                  primary={tf.path}
                  secondary={<TestNamesList names={tf.test_names} />}
                  primaryTypographyProps={{ variant: 'body2', fontFamily: 'monospace' }}
                  secondaryTypographyProps={{ variant: 'body2', sx: { mt: 0.5 } }}
                />
              </ListItem>
            ))}
          </List>
        )}
      </Paper>

      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 1 }}>
        Commits
      </Typography>
      <Paper sx={{ border: '1px solid rgba(148, 163, 184, 0.35)', boxShadow: 'none' }}>
        {commits.length === 0 ? (
          <Box sx={{ p: 2, color: 'text.secondary' }}>No commits.</Box>
        ) : (
          <List dense disablePadding>
            {commits.map((c, i) => (
              <ListItem key={i} divider={i < commits.length - 1}>
                <ListItemText
                  primary={c.message || '(no message)'}
                  secondary={c.sha}
                  primaryTypographyProps={{ variant: 'body2' }}
                  secondaryTypographyProps={{ variant: 'caption' }}
                />
              </ListItem>
            ))}
          </List>
        )}
      </Paper>
    </Box>
  );
};

export default ReviewDetailPage;
