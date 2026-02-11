import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import {
  Box,
  Typography,
  Paper,
  Grid,
  Card,
  CardContent,
  CardActions,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
} from '@mui/material';
import { getProject, updateProject, type Project } from '../utils/api';

const ProjectPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editProjectPath, setEditProjectPath] = useState('');
  const [editGithubUrl, setEditGithubUrl] = useState('');

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

  return (
    <Box>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 3, flexWrap: 'wrap', gap: 2 }}>
        <Typography variant="h4">{project.name}</Typography>
        <Button variant="outlined" size="small" onClick={openEdit}>
          Edit project
        </Button>
      </Box>

      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper sx={{ p: 3 }}>
            {project.description && (
              <Typography variant="body1" sx={{ mb: 2 }}>
                {project.description}
              </Typography>
            )}
            {project.project_path && (
              <Typography variant="body2" color="textSecondary">
                Project Path: {project.project_path}
              </Typography>
            )}
            {project.github_url && (
              <Typography variant="body2" color="textSecondary">
                GitHub URL: {project.github_url}
              </Typography>
            )}
          </Paper>
        </Grid>

        <Grid item xs={12}>
          <Typography variant="h5" sx={{ mb: 2 }}>
            Tools
          </Typography>
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={4}>
              <Card component={Link} to={`/projects/${projectId}/graph`} sx={{ textDecoration: 'none', height: '100%' }}>
                <CardContent>
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    Graph Editor
                  </Typography>
                  <Typography variant="body2" color="textSecondary">
                    Visual architecture diagram editor
                  </Typography>
                </CardContent>
                <CardActions>
                  <Button size="small">Open</Button>
                </CardActions>
              </Card>
            </Grid>
            <Grid item xs={12} sm={6} md={4}>
              <Card component={Link} to={`/projects/${projectId}/kanban`} sx={{ textDecoration: 'none', height: '100%' }}>
                <CardContent>
                  <Typography variant="h6" sx={{ mb: 1 }}>
                    Kanban Board
                  </Typography>
                  <Typography variant="body2" color="textSecondary">
                    Ticket management and workflow
                  </Typography>
                </CardContent>
                <CardActions>
                  <Button size="small">Open</Button>
                </CardActions>
              </Card>
            </Grid>
          </Grid>
        </Grid>
      </Grid>

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
              placeholder="Local path for Claude Code"
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
