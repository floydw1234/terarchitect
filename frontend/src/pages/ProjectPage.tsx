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
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Chip,
} from '@mui/material';
import { getProject, getTickets, updateProject, deleteProject, type Project, type ProjectExecutionMode, type ProjectGitMode, type Ticket } from '../utils/api';
import { LineageField } from '../components/LineageField';

const ProjectPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editGithubUrl, setEditGithubUrl] = useState('');
  const [editExecutionMode, setEditExecutionMode] = useState<ProjectExecutionMode>('docker');
  const [editGitMode, setEditGitMode] = useState<ProjectGitMode>('swarm');
  const [editProjectPath, setEditProjectPath] = useState('');
  const [editWorkflowFile, setEditWorkflowFile] = useState('');
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
      const [projectData, ticketData] = await Promise.all([
        getProject(projectId),
        getTickets(projectId).catch(() => []),
      ]);
      setProject(projectData);
      setTickets(ticketData);
    } catch (error) {
      console.error('Failed to fetch project:', error);
    } finally {
      setLoading(false);
    }
  };

  const openEdit = async () => {
    if (!projectId) return;
    try {
      const data = await getProject(projectId);
      setProject(data);
      setEditName(data.name);
      setEditDescription(data.description ?? '');
      setEditGithubUrl(data.github_url ?? '');
      setEditExecutionMode(data.execution_mode ?? 'docker');
      setEditGitMode(data.git_mode ?? 'swarm');
      setEditProjectPath(data.project_path ?? '');
      setEditWorkflowFile(data.workflow_file ?? '');
      setEditOpen(true);
    } catch (error) {
      console.error('Failed to fetch project for edit:', error);
    }
  };

  const handleSaveProject = async () => {
    if (!projectId) return;
    try {
      const data = await updateProject(projectId, {
        name: editName.trim() || project?.name,
        description: editDescription.trim() || undefined,
        github_url: editGithubUrl.trim() || undefined,
        execution_mode: editExecutionMode,
        git_mode: editGitMode,
        project_path: editExecutionMode === 'local' ? (editProjectPath.trim() || null) : null,
        workflow_file: editWorkflowFile.trim() || null,
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

  const sourceTypeLabel =
    project.source_type === 'local_path'
      ? 'Local path (legacy/optional)'
      : project.source_type === 'github'
        ? 'GitHub (recommended)'
        : project.source_type;

  const latestTicketSummaries = tickets
    .filter((ticket) => ticket.latest_attempt)
    .slice(0, 4);

  const toolCardSx = {
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'space-between',
    textDecoration: 'none',
    color: 'text.primary',
    minHeight: 170,
    p: 3,
    borderRadius: 2,
    border: '1px solid #e0e0e0',
    backgroundColor: '#ffffff',
    transition: 'border-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease',
    '&:hover': {
      borderColor: '#0085fa',
      boxShadow: '0 4px 12px rgba(0, 133, 250, 0.12)',
      transform: 'translateY(-1px)',
    },
  } as const;

  return (
    <Box sx={{ maxWidth: 1100, mx: 'auto', width: '100%' }}>
      <Paper
        sx={{
          p: { xs: 2, md: 3 },
          border: '1px solid #e0e0e0',
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
          {sourceTypeLabel && (
            <Typography sx={infoTextSx}>
              Source: {sourceTypeLabel}
            </Typography>
          )}
          <Typography sx={infoTextSx}>
            Agent execution: {project.execution_mode === 'local' ? 'Local' : 'Docker'}
          </Typography>
          <Typography sx={infoTextSx}>
            Git mode:{' '}
            <span style={{ color: project.git_mode === 'swarm' ? '#0085fa' : '#4169e1' }}>
              {project.git_mode === 'swarm' ? 'Swarm (AgentHub)' : 'Legacy structured'}
            </span>
          </Typography>
          {project.execution_mode === 'local' && project.project_path && (
            <Typography sx={infoTextSx}>
              Project path: {project.project_path}
            </Typography>
          )}
          {!project.project_path && (
            <Typography sx={infoTextSx}>
              Tickets start from AgentHub frontier. No local project path is configured.
            </Typography>
          )}
          {project.github_url && (
            <Typography sx={infoTextSx}>
              GitHub URL: {project.github_url}
            </Typography>
          )}
          {(project.github_ref || project.base_ref) && (
            <Typography sx={infoTextSx}>
              GitHub ref: {project.github_ref ?? project.base_ref}
            </Typography>
          )}
          {project.github_resolved_sha && (
            <Typography sx={infoTextSx}>
              Source SHA: {project.github_resolved_sha}
            </Typography>
          )}
          {typeof project.import_to_agenthub === 'boolean' && (
            <Typography sx={infoTextSx}>
              Import to AgentHub: {project.import_to_agenthub ? 'Yes' : 'No'}
            </Typography>
          )}
          <Box sx={{ display: 'flex', gap: 0.75, flexWrap: 'wrap', pt: 0.5 }}>
            <Chip label="AgentHub DAG source of truth" size="small" variant="outlined" color="info" />
            {!project.project_path && <Chip label="No local path mode" size="small" variant="outlined" />}
          </Box>
          <LineageField
            label="Accepted frontier"
            value={project.accepted_frontier_id ?? project.shipped_frontier}
          />
          {project.github_resolved_sha && (
            <LineageField label="Source base" value={project.github_resolved_sha} />
          )}
          {project.shipped_frontier && project.accepted_frontier_id && project.shipped_frontier !== project.accepted_frontier_id && (
            <LineageField label="Shipped frontier" value={project.shipped_frontier} />
          )}
          {project.frontier_warning && (
            <Typography sx={{ ...infoTextSx, color: 'warning.main' }}>
              {project.frontier_warning}
            </Typography>
          )}
        </Stack>
      </Paper>

      {latestTicketSummaries.length > 0 && (
        <Paper
          sx={{
            p: { xs: 2, md: 3 },
            border: '1px solid #e0e0e0',
            boxShadow: 'none',
            mb: 3,
          }}
        >
          <Typography variant="h6" sx={{ mb: 1 }}>
            Recent ticket attempts
          </Typography>
          <Typography color="text.secondary" sx={{ mb: 2, fontSize: '0.95rem' }}>
            Tickets start from the current AgentHub frontier.
          </Typography>
          <Stack spacing={1.25}>
            {latestTicketSummaries.map((ticket) => (
              <Box
                key={ticket.id}
                sx={{
                  p: 1.5,
                  border: '1px solid #e0e0e0',
                  borderRadius: 1.5,
                }}
              >
                <Stack direction={{ xs: 'column', sm: 'row' }} justifyContent="space-between" spacing={1}>
                  <Box sx={{ minWidth: 0 }}>
                    <Typography sx={{ fontWeight: 600 }}>
                      {ticket.title}
                    </Typography>
                    <Typography color="text.secondary" sx={{ fontSize: '0.9rem' }}>
                      Latest attempt: {ticket.latest_attempt?.status}
                      {ticket.latest_attempt?.short_commit_hash ? ` · ${ticket.latest_attempt.short_commit_hash}` : ''}
                    </Typography>
                  </Box>
                  <Stack direction="row" spacing={0.75} flexWrap="wrap">
                    {ticket.latest_attempt?.stale === true && (
                      <Chip label="Stale" size="small" color="warning" variant="outlined" />
                    )}
                    {ticket.latest_attempt?.stale === false && (
                      <Chip label="Current" size="small" color="success" variant="outlined" />
                    )}
                    {ticket.latest_attempt?.accepted_frontier_id && (
                      <Chip
                        label={`Frontier ${ticket.latest_attempt.accepted_frontier_id.slice(0, 12)}`}
                        size="small"
                        variant="outlined"
                      />
                    )}
                  </Stack>
                </Stack>
                {ticket.latest_attempt?.stale_reason && (
                  <Typography color="warning.main" sx={{ fontSize: '0.85rem', mt: 0.75 }}>
                    {ticket.latest_attempt.stale_reason}
                  </Typography>
                )}
              </Box>
            ))}
          </Stack>
        </Paper>
      )}

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

        <Grid item xs={12} sm={6}>
          <Paper component={Link} to={`/projects/${projectId}/ship`} sx={toolCardSx}>
            <Box>
              <Typography variant="h6" sx={{ mb: 1 }}>
                Ship Room
              </Typography>
              <Typography variant="body2" color="text.secondary">
                Compose accepted attempts and ship release artifacts
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
            <FormControl size="small" fullWidth>
              <InputLabel>Agent execution</InputLabel>
              <Select
                value={editExecutionMode}
                label="Agent execution"
                onChange={(e) => setEditExecutionMode(e.target.value as ProjectExecutionMode)}
              >
                <MenuItem value="docker">Docker (clone repo in container)</MenuItem>
                <MenuItem value="local">Local (run on host at project path)</MenuItem>
              </Select>
            </FormControl>
            <FormControl size="small" fullWidth>
              <InputLabel>Git mode</InputLabel>
              <Select
                value={editGitMode}
                label="Git mode"
                onChange={(e) => setEditGitMode(e.target.value as ProjectGitMode)}
              >
                <MenuItem value="swarm">Swarm — AgentHub DAG (no PRs)</MenuItem>
              </Select>
            </FormControl>
            {editExecutionMode === 'local' && (
              <TextField
                label="Project path"
                value={editProjectPath}
                onChange={(e) => setEditProjectPath(e.target.value)}
                placeholder="/path/to/project/on/host"
                helperText="Path on the machine where the coordinator runs; agent will use this directory instead of cloning."
                fullWidth
                size="small"
              />
            )}
            <TextField
              label="GitHub URL"
              value={editGithubUrl}
              onChange={(e) => setEditGithubUrl(e.target.value)}
              placeholder="https://github.com/..."
              helperText={editExecutionMode === 'docker' ? 'Required for Docker (clone). Optional for Local.' : undefined}
              fullWidth
              size="small"
            />
            <TextField
              label="Workflow file"
              value={editWorkflowFile}
              onChange={(e) => setEditWorkflowFile(e.target.value)}
              placeholder=".terarchitect/workflow.yaml"
              helperText="Path to custom workflow definition relative to project root (JSON/YAML). Leave empty for default + auto-discover."
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
