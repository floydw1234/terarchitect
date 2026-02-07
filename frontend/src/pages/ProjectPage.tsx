import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { Box, Typography, Paper, Grid, Card, CardContent, CardActions, Button } from '@mui/material';

interface Project {
  id: string;
  name: string;
  description: string;
  git_repo_path: string;
}

const ProjectPage: React.FC = () => {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchProject();
  }, [projectId]);

  const fetchProject = async () => {
    try {
      const response = await fetch(`/api/projects/${projectId}`);
      const data = await response.json();
      setProject(data);
    } catch (error) {
      console.error('Failed to fetch project:', error);
    } finally {
      setLoading(false);
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
      <Typography variant="h4" sx={{ mb: 3 }}>
        {project.name}
      </Typography>

      <Grid container spacing={3}>
        <Grid item xs={12}>
          <Paper sx={{ p: 3, backgroundColor: '#1a1a2e' }}>
            {project.description && (
              <Typography variant="body1" sx={{ mb: 2 }}>
                {project.description}
              </Typography>
            )}
            {project.git_repo_path && (
              <Typography variant="body2" color="textSecondary">
                Git Repository: {project.git_repo_path}
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
    </Box>
  );
};

export default ProjectPage;
