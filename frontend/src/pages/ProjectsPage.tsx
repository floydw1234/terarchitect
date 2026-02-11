import React, { useState, useEffect } from 'react';
import { Box, Typography, Button, Card, CardContent, CardActions, Grid, Paper, TextField } from '@mui/material';
import { Link } from 'react-router-dom';
import { getProjects, createProject, type Project } from '../utils/api';

const ProjectsPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [projectPath, setProjectPath] = useState('');
  const [githubUrl, setGithubUrl] = useState('');

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
      setProjects((prev) => [...prev, data]);
    } catch (error) {
      console.error('Failed to create project:', error);
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
    <Box>
      <Typography variant="h4" sx={{ mb: 3 }}>
        Projects
      </Typography>

      {/* Create Project Form */}
      <Paper sx={{ p: 3, mb: 4 }}>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Create New Project
        </Typography>
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label="Project Name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Enter project name"
          />
          <TextField
            label="Description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Enter project description"
            multiline
            rows={2}
          />
          <TextField
            label="Project Directory Path"
            value={projectPath}
            onChange={(e) => setProjectPath(e.target.value)}
            placeholder="/path/to/project/root"
            helperText="Local file path where Claude Code will run (contains codebase)"
          />
          <TextField
            label="GitHub Repository URL"
            value={githubUrl}
            onChange={(e) => setGithubUrl(e.target.value)}
            placeholder="https://github.com/username/repo"
            helperText="GitHub repository URL for PR creation"
          />
          <Button
            variant="contained"
            color="primary"
            onClick={handleCreateProject}
            disabled={!name.trim()}
          >
            Create Project
          </Button>
        </Box>
      </Paper>

      {/* Projects Grid */}
      {projects.length === 0 ? (
        <Paper sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="textSecondary">
            No projects yet. Create one to get started!
          </Typography>
        </Paper>
      ) : (
        <Grid container spacing={3}>
          {projects.map((project) => (
            <Grid item xs={12} sm={6} md={4} key={project.id}>
              <Card sx={{ height: '100%' }}>
                <CardContent>
                  <Typography variant="h6" component={Link} to={`/projects/${project.id}`} sx={{
                    textDecoration: 'none',
                    color: 'primary.main',
                    fontWeight: 600,
                  }}>
                    {project.name}
                  </Typography>
                  {project.description && (
                    <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
                      {project.description}
                    </Typography>
                  )}
                  {project.project_path && (
                    <Typography variant="body2" color="textSecondary" sx={{ mt: 1, fontSize: '0.75rem' }}>
                      Path: {project.project_path}
                    </Typography>
                  )}
                  {project.github_url && (
                    <Typography variant="body2" color="textSecondary" sx={{ mt: 1, fontSize: '0.75rem' }}>
                      GitHub: {project.github_url}
                    </Typography>
                  )}
                </CardContent>
                <CardActions>
                  <Button
                    component={Link}
                    to={`/projects/${project.id}/graph`}
                    size="small"
                    color="primary"
                  >
                    Graph
                  </Button>
                  <Button
                    component={Link}
                    to={`/projects/${project.id}/kanban`}
                    size="small"
                    color="primary"
                  >
                    Kanban
                  </Button>
                </CardActions>
              </Card>
            </Grid>
          ))}
        </Grid>
      )}
    </Box>
  );
};

export default ProjectsPage;
