import React, { useState, useEffect } from 'react';
import {
  Box,
  Typography,
  Button,
  Grid,
  Paper,
  TextField,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Stack,
} from '@mui/material';
import { Link } from 'react-router-dom';
import { getProjects, createProject, deleteProject, type Project } from '../utils/api';

const ProjectsPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [projectPath, setProjectPath] = useState('');
  const [githubUrl, setGithubUrl] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [deleteConfirmName, setDeleteConfirmName] = useState('');
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);

  useEffect(() => {
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const data = await getProjects();
      setProjects(data);
    } catch (error) {
      console.error('Failed to fetch projects:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleCreateProject = async () => {
    if (!name.trim()) return;

    try {
      const data = await createProject({
        name,
        description,
        project_path: projectPath || undefined,
        github_url: githubUrl || undefined,
      });

      setName('');
      setDescription('');
      setProjectPath('');
      setGithubUrl('');
      setCreateOpen(false);
      setProjects((prev) => [...prev, data]);
    } catch (error) {
      console.error('Failed to create project:', error);
    }
  };

  const openDelete = (project: Project) => {
    setDeleteTarget(project);
    setDeleteConfirmName('');
  };

  const handleDeleteProject = async () => {
    if (!deleteTarget) return;
    if (deleteConfirmName.trim() !== deleteTarget.name) return;
    setDeleteSubmitting(true);
    try {
      await deleteProject(deleteTarget.id, deleteConfirmName.trim());
      setProjects((prev) => prev.filter((p) => p.id !== deleteTarget.id));
      setDeleteTarget(null);
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

  return (
    <Box sx={{ maxWidth: 1200, mx: 'auto', width: '100%' }}>
      <Paper
        sx={{
          p: { xs: 2, md: 3 },
          mb: 3,
          border: '1px solid rgba(148, 163, 184, 0.45)',
          boxShadow: 'none',
        }}
      >
        <Stack
          direction={{ xs: 'column', md: 'row' }}
          alignItems={{ xs: 'flex-start', md: 'center' }}
          justifyContent="space-between"
          spacing={2}
        >
          <Box>
            <Typography variant="h4">Projects</Typography>
            <Typography color="text.secondary" sx={{ mt: 0.5 }}>
              Manage and open your project workspaces.
            </Typography>
          </Box>
          <Button variant="contained" onClick={() => setCreateOpen(true)}>
            Create
          </Button>
        </Stack>
      </Paper>

      {/* Projects Grid */}
      {projects.length === 0 ? (
        <Paper
          sx={{
            p: 4,
            textAlign: 'center',
            border: '1px solid rgba(148, 163, 184, 0.45)',
            boxShadow: 'none',
          }}
        >
          <Typography color="text.secondary">
            No projects yet. Create one to get started!
          </Typography>
        </Paper>
      ) : (
        <Grid container spacing={3}>
          {projects.map((project) => (
            <Grid item xs={12} sm={6} md={4} key={project.id}>
              <Paper
                sx={{
                  height: '100%',
                  p: 3,
                  border: '1px solid rgba(148, 163, 184, 0.45)',
                  boxShadow: 'none',
                  display: 'flex',
                  flexDirection: 'column',
                }}
              >
                <Typography
                  variant="h6"
                  component={Link}
                  to={`/projects/${project.id}`}
                  sx={{ textDecoration: 'none', color: 'primary.main', fontWeight: 600, mb: 1 }}
                >
                  {project.name}
                </Typography>
                {project.description && (
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                    {project.description}
                  </Typography>
                )}
                {project.project_path && (
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem', mb: 0.5 }}>
                    Path: {project.project_path}
                  </Typography>
                )}
                {project.github_url && (
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                    GitHub: {project.github_url}
                  </Typography>
                )}

                <Box sx={{ mt: 'auto', pt: 2, display: 'flex', justifyContent: 'space-between', gap: 1, flexWrap: 'wrap' }}>
                  <Box>
                    <Button
                      component={Link}
                      to={`/projects/${project.id}/graph`}
                      size="small"
                    >
                      Graph
                    </Button>
                    <Button
                      component={Link}
                      to={`/projects/${project.id}/kanban`}
                      size="small"
                    >
                      Kanban
                    </Button>
                  </Box>
                  <Button
                    size="small"
                    color="error"
                    onClick={() => openDelete(project)}
                  >
                    Delete
                  </Button>
                </Box>
              </Paper>
            </Grid>
          ))}
        </Grid>
      )}

      <Dialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Create project</DialogTitle>
        <DialogContent>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
            <TextField
              label="Project Name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter project name"
              fullWidth
              size="small"
            />
            <TextField
              label="Description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Enter project description"
              multiline
              minRows={2}
              fullWidth
              size="small"
            />
            <TextField
              label="Project Directory Path"
              value={projectPath}
              onChange={(e) => setProjectPath(e.target.value)}
              placeholder="/path/to/project/root"
              helperText="Local file path where OpenCode will run (contains codebase)"
              fullWidth
              size="small"
            />
            <TextField
              label="GitHub Repository URL"
              value={githubUrl}
              onChange={(e) => setGithubUrl(e.target.value)}
              placeholder="https://github.com/username/repo"
              helperText="GitHub repository URL for PR creation"
              fullWidth
              size="small"
            />
          </Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleCreateProject}
            disabled={!name.trim()}
          >
            Create
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        onClose={() => !deleteSubmitting && setDeleteTarget(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Delete project</DialogTitle>
        <DialogContent>
          {deleteTarget && (
            <>
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
                placeholder={deleteTarget.name}
                fullWidth
                size="small"
                autoComplete="off"
                error={deleteConfirmName.length > 0 && deleteConfirmName !== deleteTarget.name}
                helperText={deleteConfirmName.length > 0 && deleteConfirmName !== deleteTarget.name ? 'Must match the project name exactly' : ''}
              />
            </>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDeleteTarget(null)} disabled={deleteSubmitting}>Cancel</Button>
          <Button
            variant="contained"
            color="error"
            onClick={handleDeleteProject}
            disabled={!deleteTarget || deleteConfirmName.trim() !== deleteTarget.name || deleteSubmitting}
          >
            {deleteSubmitting ? 'Deleting…' : 'Delete project'}
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
};

export default ProjectsPage;
