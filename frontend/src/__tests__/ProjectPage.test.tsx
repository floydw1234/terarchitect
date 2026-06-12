import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  updateProject: jest.fn(),
  deleteProject: jest.fn(),
}));

import * as api from '../utils/api';
import ProjectPage from '../pages/ProjectPage';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderProjectPage() {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/projects/proj-1']}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
});

test('project detail shows GitHub source metadata when present', async () => {
  (api.getProject as jest.Mock).mockResolvedValue({
    id: 'proj-1',
    name: 'Wizard',
    description: 'GitHub-first project',
    source_type: 'github',
    github_url: 'https://github.com/acme/wizard',
    github_ref: 'release/2026.06',
    github_resolved_sha: '1234567890abcdef1234567890abcdef12345678',
    import_to_agenthub: true,
    execution_mode: 'docker',
    git_mode: 'swarm',
    accepted_frontier_id: 'f'.repeat(40),
    shipped_frontier: 'e'.repeat(40),
  });

  renderProjectPage();

  await waitFor(() => {
    expect(screen.getByText('Wizard')).toBeInTheDocument();
    expect(screen.getByText('Source: GitHub repository')).toBeInTheDocument();
    expect(screen.getByText('GitHub URL: https://github.com/acme/wizard')).toBeInTheDocument();
    expect(screen.getByText('GitHub ref: release/2026.06')).toBeInTheDocument();
    expect(screen.getByText('Resolved SHA: 1234567890abcdef1234567890abcdef12345678')).toBeInTheDocument();
    expect(screen.getByText('Import to AgentHub: Yes')).toBeInTheDocument();
    expect(screen.getByText('Accepted frontier')).toBeInTheDocument();
  });
});
