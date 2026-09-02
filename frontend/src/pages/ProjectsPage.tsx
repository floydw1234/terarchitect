import React, { useState, useEffect } from 'react';
import {
  Alert,
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
  FormControl,
  InputLabel,
  Select,
  MenuItem,
  Checkbox,
  FormControlLabel,
  Chip,
} from '@mui/material';
import { Link } from 'react-router-dom';
import { getProjects, createProject, deleteProject, getExecutionReady, type Project, type ProjectExecutionMode, type ProjectGitMode, type ProjectSourceType } from '../utils/api';
import { LineageField } from '../components/LineageField';

function getSourceTypeLabel(sourceType?: string) {
  if (sourceType === 'local_path') {
    return 'Local path (legacy/optional)';
  }
  if (sourceType === 'github') {
    return 'GitHub (recommended)';
  }
  return sourceType ?? 'Unknown';
}

const ProjectsPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [sourceType, setSourceType] = useState<ProjectSourceType>('github');
  const [githubUrl, setGithubUrl] = useState('');
  const [baseRef, setBaseRef] = useState('main');
  const [importToAgenthub, setImportToAgenthub] = useState(true);
  const [executionMode, setExecutionMode] = useState<ProjectExecutionMode>('docker');
  const [gitMode, setGitMode] = useState<ProjectGitMode>('swarm');
  const [projectPath, setProjectPath] = useState('');
  const [projectType, setProjectType] = useState<'new' | 'existing'>('new');
  const [workflowFile, setWorkflowFile] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [deleteConfirmName, setDeleteConfirmName] = useState('');
  const [deleteSubmitting, setDeleteSubmitting] = useState(false);
  const [frontierWarning, setFrontierWarning] = useState<string | null>(null);
  const [workspaceEnabled, setWorkspaceEnabled] = useState(false);

  useEffect(() => {
    fetchProjects();
    getExecutionReady()
      .then(r => setWorkspaceEnabled(r.features?.composite_workspace ?? false))
      .catch(() => {});
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
    if (sourceType === 'github' && !githubUrl.trim()) return;
    if (sourceType === 'local_path' && !projectPath.trim()) return;

    const normalizedBaseRef = baseRef.trim() || 'main';

    try {
      const data = await createProject({
        name: name.trim(),
        description: description.trim() || undefined,
        source_type: sourceType,
        github_url: sourceType === 'github' ? (githubUrl.trim() || undefined) : undefined,
        base_ref: sourceType === 'github' ? normalizedBaseRef : undefined,
        github_ref: sourceType === 'github' ? normalizedBaseRef : undefined,
        import_to_agenthub: sourceType === 'github' ? importToAgenthub : undefined,
        execution_mode: executionMode,
        git_mode: gitMode,
        project_path: sourceType === 'local_path' ? (projectPath.trim() || undefined) : undefined,
        is_existing_repo: projectType === 'existing',
        workflow_file: workflowFile.trim() || undefined,
      });

      setName('');
      setDescription('');
      setSourceType('github');
      setGithubUrl('');
      setBaseRef('main');
      setImportToAgenthub(true);
      setExecutionMode('docker');
      setGitMode('swarm');
      setProjectPath('');
      setProjectType('new');
      setWorkflowFile('');
      setCreateOpen(false);
      setProjects((prev) => [...prev, data]);
      if (data.frontier_warning) {
        setFrontierWarning(data.frontier_warning);
      }
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

  const isGithubSource = sourceType === 'github';
  const createDisabled = !name.trim() || (isGithubSource ? !githubUrl.trim() : !projectPath.trim());

  return (
    <Box sx={{ maxWidth: 1200, mx: 'auto', width: '100%' }}>
      <Paper
        sx={{
          p: { xs: 2, md: 3 },
          mb: 3,
          border: '1px solid #D4D4D4',
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

      {frontierWarning && (
        <Alert severity="warning" onClose={() => setFrontierWarning(null)} sx={{ mb: 2 }}>
          <strong>Frontier not set:</strong> {frontierWarning}
        </Alert>
      )}

      {/* Projects Grid */}
      {projects.length === 0 ? (
        <Paper
          sx={{
            p: 4,
            textAlign: 'center',
            border: '1px solid #D4D4D4',
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
                  border: '1px solid #D4D4D4',
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
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem', mb: 0.5 }}>
                  {project.execution_mode === 'local' ? 'Local' : 'Docker'}
                  {project.execution_mode === 'local' && project.project_path ? ` · ${project.project_path}` : ''}
                </Typography>
                <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem', mb: 0.5 }}>
                  Source: {getSourceTypeLabel(project.source_type)}
                </Typography>
                {project.github_url && (
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                    GitHub: {project.github_url}
                  </Typography>
                )}
                {(project.github_ref || project.base_ref) && (
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
                    Ref: {project.github_ref ?? project.base_ref}
                  </Typography>
                )}
                {!project.project_path && (
                  <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem', mt: 0.5 }}>
                    Tickets start from AgentHub frontier. No local path required.
                  </Typography>
                )}
                <Box sx={{ mt: 1 }}>
                  <LineageField
                    label="Accepted frontier"
                    value={project.accepted_frontier_id ?? project.shipped_frontier}
                  />
                </Box>
                {project.accepted_frontier_id && project.github_resolved_sha && (
                  <Box sx={{ mt: 0.5, display: 'flex', gap: 0.75, flexWrap: 'wrap' }}>
                    <Chip label="AgentHub source of truth" size="small" variant="outlined" color="info" />
                    <Chip label={`Base ${project.github_resolved_sha.slice(0, 12)}`} size="small" variant="outlined" />
                  </Box>
                )}
                {project.shipped_frontier && project.accepted_frontier_id && project.shipped_frontier !== project.accepted_frontier_id && (
                  <Box sx={{ mt: 0.5 }}>
                    <LineageField label="Shipped frontier" value={project.shipped_frontier} />
                  </Box>
                )}
                {!project.accepted_frontier_id && !project.shipped_frontier && (
                  <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 0.5 }}>
                    ⚠ Frontier not set — set via Ship Room before running agents
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
                    <Button
                      component={Link}
                      to={`/projects/${project.id}/ship`}
                      size="small"
                    >
                      Ship Room
                    </Button>
                    {workspaceEnabled && (
                      <Button
                        component={Link}
                        to={`/projects/${project.id}/workspace`}
                        size="small"
                      >
                        Workspace
                      </Button>
                    )}
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
            <FormControl size="small" fullWidth>
              <InputLabel>Project type</InputLabel>
              <Select
                value={projectType}
                label="Project type"
                onChange={(e) => setProjectType(e.target.value as 'new' | 'existing')}
              >
                <MenuItem value="new">New project</MenuItem>
                <MenuItem value="existing">Existing project</MenuItem>
              </Select>
              {projectType === 'existing' && (
                <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
                  No &quot;Project setup&quot; ticket will be added (repo already has structure).
                </Typography>
              )}
            </FormControl>
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
            <FormControl size="small" fullWidth>
              <InputLabel>Source</InputLabel>
              <Select
                value={sourceType}
                label="Source"
                onChange={(e) => {
                  const nextSource = e.target.value as ProjectSourceType;
                  setSourceType(nextSource);
                  if (nextSource === 'github') {
                    setExecutionMode('docker');
                  } else {
                    setExecutionMode('local');
                  }
                }}
              >
                <MenuItem value="github">GitHub repository (recommended)</MenuItem>
                <MenuItem value="local_path">Local path (legacy/optional)</MenuItem>
              </Select>
            </FormControl>
            {isGithubSource ? (
              <>
                <TextField
                  label="GitHub Repository URL"
                  value={githubUrl}
                  onChange={(e) => setGithubUrl(e.target.value)}
                  placeholder="https://github.com/owner/repo"
                  helperText="Recommended default. AgentHub frontier becomes the source of truth after import."
                  fullWidth
                  required
                  size="small"
                />
                <TextField
                  label="Base ref"
                  value={baseRef}
                  onChange={(e) => setBaseRef(e.target.value)}
                  placeholder="main"
                  helperText="Branch, tag, or commit to import from. Defaults to main."
                  fullWidth
                  size="small"
                />
                <FormControlLabel
                  control={
                    <Checkbox
                      checked={importToAgenthub}
                      onChange={(e) => setImportToAgenthub(e.target.checked)}
                    />
                  }
                  label="Import to AgentHub frontier"
                />
              </>
            ) : (
              <TextField
                label="Project path"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                placeholder="/path/to/project/on/host"
                helperText="Legacy/advanced mode on the coordinator host. Optional unless you need host-local execution."
                fullWidth
                required
                size="small"
              />
            )}
            <FormControl size="small" fullWidth>
              <InputLabel>Agent execution</InputLabel>
              <Select
                value={executionMode}
                label="Agent execution"
                onChange={(e) => setExecutionMode(e.target.value as ProjectExecutionMode)}
              >
                {sourceType === 'github' && (
                  <MenuItem value="docker">Docker (recommended)</MenuItem>
                )}
                <MenuItem value="local">Local</MenuItem>
              </Select>
            </FormControl>
            {executionMode === 'local' && sourceType === 'github' && (
              <TextField
                label="Project path"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                placeholder="/path/to/project/on/host"
                helperText="Optional override for local execution against an already-checked-out repo."
                fullWidth
                size="small"
              />
            )}
            <FormControl size="small" fullWidth>
              <InputLabel>Git mode</InputLabel>
              <Select
                value={gitMode}
                label="Git mode"
                onChange={(e) => setGitMode(e.target.value as ProjectGitMode)}
              >
                <MenuItem value="swarm">AgentHub (swarm) — recommended</MenuItem>
              </Select>
            </FormControl>
            <TextField
              label="Workflow file (optional)"
              value={workflowFile}
              onChange={(e) => setWorkflowFile(e.target.value)}
              placeholder=".terarchitect/workflow.yaml"
              helperText="Path to custom workflow definition (JSON/YAML). Default discovered from .terarchitect/ if not set."
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
            disabled={createDisabled}
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
