import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Box,
  Typography,
  Paper,
  List,
  ListItem,
  ListItemButton,
  ListItemText,
  CircularProgress,
  Tooltip,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import ScheduleIcon from '@mui/icons-material/Schedule';
import { getProject, getReviewList, type Project, type ReviewListEntry } from '../utils/api';

const ReviewPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [tickets, setTickets] = useState<ReviewListEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (projectId) {
      Promise.all([getProject(projectId), getReviewList(projectId)])
        .then(([p, t]) => {
          setProject(p);
          setTickets(t);
        })
        .catch((e) => console.error(e))
        .finally(() => setLoading(false));
    }
  }, [projectId]);

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 200 }}>
        <CircularProgress />
      </Box>
    );
  }

  if (!project) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 200 }}>
        <Typography>Project not found</Typography>
      </Box>
    );
  }

  const allPrsUrl = project.github_url
    ? `${project.github_url.replace(/\/$/, '')}/pulls`
    : null;

  return (
    <Box sx={{ maxWidth: 700, mx: 'auto', width: '100%' }}>
      <Typography variant="h5" sx={{ mb: 2 }}>
        Review
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        {project.name} — up to 20 most recent PRs (pending first). Open one to see summary and commits.
      </Typography>
      {allPrsUrl && (
        <Typography variant="body2" sx={{ mb: 2 }}>
          <a href={allPrsUrl} target="_blank" rel="noopener noreferrer" style={{ color: 'inherit' }}>
            View all PRs on GitHub →
          </a>
        </Typography>
      )}
      <Paper sx={{ border: '1px solid rgba(148, 163, 184, 0.35)', boxShadow: 'none' }}>
        {tickets.length === 0 ? (
          <Box sx={{ p: 3, textAlign: 'center', color: 'text.secondary' }}>
            No tickets with PRs yet. Move a ticket to In Review (after the agent creates a PR) to see it here.
          </Box>
        ) : (
          <List disablePadding>
            {tickets.map((t) => (
              <ListItem key={t.id} disablePadding divider>
                <ListItemButton component={Link} to={`/projects/${projectId}/review/${t.id}`}>
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, width: '100%' }}>
                    {t.merged ? (
                      <Tooltip title="Merged">
                        <CheckCircleIcon fontSize="small" color="success" />
                      </Tooltip>
                    ) : (
                      <Tooltip title="Pending">
                        <ScheduleIcon fontSize="small" color="action" />
                      </Tooltip>
                    )}
                    <ListItemText
                      primary={t.title}
                      secondary={`PR #${t.pr_number}`}
                    />
                  </Box>
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        )}
      </Paper>
      <Box sx={{ mt: 2 }}>
        <Typography component={Link} to={`/projects/${projectId}`} sx={{ color: 'primary.main', textDecoration: 'none' }}>
          ← Back to project
        </Typography>
      </Box>
    </Box>
  );
};

export default ReviewPage;
