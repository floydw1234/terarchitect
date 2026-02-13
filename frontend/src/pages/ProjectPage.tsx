import React, { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import {
  Box,
  Typography,
  Paper,
  Grid,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Stack,
} from '@mui/material';
import { getProject, updateProject, deleteProject, type Project } from '../utils/api';

const ProjectPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editProjectPath, setEditProjectPath] = useState('');
  const [editGithubUrl, setEditGithubUrl] = useState('');
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteConfirmName, setDeleteConfirmName] = useState('');
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);

  useEffect(() => {
    if (projectId) {
      fetchProject();
    }
  }, [projectId]);

  const fetchProject = async () => {
    if (!projectId) return;
    try {
      const data = await getProject(projectId);
      setProject(data);
    } catch (error) {
      console.error('Failed to fetch project:', error);
    } finally {
      setLoading(false);
    }
  };

  const openEdit = () => {
    if (project) {
      setEditName(project.name);
      setEditDescription(project.description ?? '');
      setEditProjectPath(project.project_path ?? '');
      setEditGithubUrl(project.github_url ?? '');
      setEditOpen(true);
    }
  };

  const handleSaveProject = async () => {
    if (!projectId) return;
    try {
      const data = await updateProject(projectId, {
        name: editName.trim() || project?.name,
        description: editDescription.trim() || undefined,
        project_path: editProjectPath.trim() || undefined,
        github_url: editGithubUrl.trim() || undefined,
      });
      setProject(data);
      setEditOpen(false);
    } catch (error) {
      console.error('Failed to update project:', error);
    }
  };

  const openDelete = () => {
    setDeleteConfirmName('');
    setDeleteOpen(true);
  };

  const handleDeleteProject = async () => {
    if (!projectId || !project) return;
    if (deleteConfirmName.trim() !== project.name) return;
    setDeleteSubmitting(true);
    try {
      await deleteProject(projectId, deleteConfirmName.trim());
      navigate('/projects');
    } catch (error) {
      console.error('Failed to delete project:', error);
      setDeleteSubmitting(false);
    }
  };

  if (loading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Typography>Loading...</Typography>
      </Box>
    );
  }

  if (!project) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Typography>Project not found</Typography>
      </Box>
    );
  }

  const infoTextSx = {
    color: 'text.secondary',
    fontSize: '0.95rem',
  } as const;

  const toolCardSx = {
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'space-between',
    textDecoration: 'none',
    color: 'text.primary',
    minHeight: 170,
    p: 3,
    borderRadius: 2,
    border: '1px solid rgba(148, 163, 184, 0.45)',
    backgroundColor: 'rgba(30, 41, 59, 0.85)',
    transition: 'border-color 0.2s ease, transform 0.2s ease, background-color 0.2s ease',
    '&:hover': {
      borderColor: 'rgba(34, 211, 238, 0.7)',
      backgroundColor: 'rgba(30, 41, 59, 1)',
      transform: 'translateY(-1px)',
    },
  } as const;

  return (
    <Box sx={{ maxWidth: 1100, mx: 'auto', width: '100%' }}>
      <Paper
        sx={{
          p: { xs: 2, md: 3 },
          border: '1px solid rgba(148, 163, 184, 0.45)',
          boxShadow: 'none',
          mb: 3,
        }}
      >
        <Stack
          direction={{ xs: 'column', md: 'row' }}
          alignItems={{ xs: 'flex-start', md: 'center' }}
          justifyContent="space-between"
          spacing={2}
          sx={{ mb: 2 }}
        >
          <Typography variant="h4">{project.name}</Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button variant="outlined" size="small" onClick={openEdit}>
              Edit project
            </Button>
            <Button variant="outlined" size="small" color="error" onClick={openDelete}>
              Delete project
            </Button>
          </Box>
        </Stack>

        {project.description && (
          <Typography variant="body1" sx={{ mb: 2 }}>
            {project.description}
          </Typography>
        )}

        <Stack spacing={0.5}>
          {project.project_path && (
            <Typography sx={infoTextSx}>
              Project Path: {project.project_path}
            </Typography>
          )}
          {project.github_url && (
            <Typography sx={infoTextSx}>
              GitHub URL: {project.github_url}
            </Typography>
          )}
        </Stack>
      </Paper>

      <Typography variant="h5" sx={{ mb: 2 }}>
        Tools
      </Typography>

      <Grid container spacing={2}>
        <Grid item xs={12} sm={6}>
          <Paper component={Link} to={`/projects/${projectId}/graph`} sx={toolCardSx}>
            <Box>
              <Typography variant="h6" sx={{ mb: 1 }}>
                Graph Editor
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Visual architecture diagram editor
              </Typography>
            </Box>
            <Box sx={{ mt: 3 }}>
              <Button size="small" variant="text">Open</Button>
            </Box>
          </Paper>
        </Grid>

        <Grid item xs={12} sm={6}>
          <Paper component={Link} to={`/projects/${projectId}/kanban`} sx={toolCardSx}>
            <Box>
              <Typography variant="h6" sx={{ mb: 1 }}>
                Kanban Board
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Ticket management and workflow
              </Typography>
            </Box>
            <Box sx={{ mt: 3 }}>
              <Button size="small" variant="text">Open</Button>
            </Box>
          </Paper>
        </Grid>
      </Grid>

      <Dialog open={deleteOpen} onClose={() => !deleteSubmitting && setDeleteOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Delete project</DialogTitle>
        <DialogContent>
          <Typography color="error" sx={{ fontWeight: 600, mb: 1 }}>
            This action cannot be undone.
          </Typography>
          <Typography sx={{ mb: 2 }}>
            This will permanently delete the project and all its data: graph, kanban board, tickets, notes, execution logs, and project memory.
          </Typography>
          <TextField
            label="Type the project name to confirm"
            value={deleteConfirmName}
            onChange={(e) => setDeleteConfirmName(e.target.value)}
            placeholder={project.name}
            fullWidth
            size="small"
            autoComplete="off"
            error={deleteConfirmName.length > 0 && deleteConfirmName !== project.name}
            helperText={deleteConfirmName.length > 0 && deleteConfirmName !== project.name ? 'Must match the project name exactly' : ''}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteOpen(false)} disabled={deleteSubmitting}>Cancel</Button>
          <Button
            variant="contained"
            color="error"
            onClick={handleDeleteProject}
            disabled={deleteConfirmName.trim() !== project.name || deleteSubmitting}
          >
            {deleteSubmitting ? 'Deleting…' : 'Delete project'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog open={editOpen} onClose={() => setEditOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Edit project</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <TextField
              label="Name"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              fullWidth
              size="small"
            />
            <TextField
              label="Description"
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
              multiline
              minRows={2}
              fullWidth
              size="small"
            />
            <TextField
              label="Project path"
              value={editProjectPath}
              onChange={(e) => setEditProjectPath(e.target.value)}
              placeholder="Local path for OpenCode"
              fullWidth
              size="small"
            />
            <TextField
              label="GitHub URL"
              value={editGithubUrl}
              onChange={(e) => setEditGithubUrl(e.target.value)}
              placeholder="https://github.com/..."
              fullWidth
              size="small"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setEditOpen(false)}>Cancel</Button>
          <Button variant="contained" onClick={handleSaveProject}>Save</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ProjectPage;
