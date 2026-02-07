import React, { useState, useEffect } from 'react';
import { Box, Typography, Button, Card, CardContent, CardActions, Grid, Paper, TextField } from '@mui/material';
import { Link } from 'react-router-dom';

interface Project {
  id: string;
  name: string;
  description: string;
  git_repo_path: string;
  created_at: string;
}

const ProjectsPage: React.FC = () => {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [gitRepoPath, setGitRepoPath] = useState('');

  useEffect(() => {
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const response = await fetch('/api/projects');
      const data = await response.json();
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
      const response = await fetch('/api/projects', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          name,
          description,
          git_repo_path: gitRepoPath,
        }),
      });
      const data = await response.json();

      // Reset form
      setName('');
      setDescription('');
      setGitRepoPath('');

      // Add new project to list
      setProjects(prev => [...prev, {
        id: data.id,
        name,
        description,
        git_repo_path: gitRepoPath,
        created_at: data.created_at,
      }]);
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
      <Paper sx={{ p: 3, mb: 4, backgroundColor: '#1a1a2e' }}>
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
            label="Git Repository Path"
            value={gitRepoPath}
            onChange={(e) => setGitRepoPath(e.target.value)}
            placeholder="/path/to/local/git/repo"
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
        <Paper sx={{ p: 4, textAlign: 'center', backgroundColor: '#1a1a2e' }}>
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
                    color: '#6366f1',
                    fontWeight: 'bold',
                  }}>
                    {project.name}
                  </Typography>
                  {project.description && (
                    <Typography variant="body2" color="textSecondary" sx={{ mt: 1 }}>
                      {project.description}
                    </Typography>
                  )}
                  {project.git_repo_path && (
                    <Typography variant="body2" color="textSecondary" sx={{ mt: 1, fontSize: '0.75rem' }}>
                      Git: {project.git_repo_path}
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
