import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeContextProvider } from '../contexts/ThemeContext';

jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  getTickets: jest.fn(),
  updateProject: jest.fn(),
  deleteProject: jest.fn(),
}));

import * as api from '../utils/api';
import ProjectPage from '../pages/ProjectPage';

function renderProjectPage() {
  return render(
    <ThemeContextProvider>
      <MemoryRouter initialEntries={['/projects/proj-1']}>
        <Routes>
          <Route path="/projects/:projectId" element={<ProjectPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeContextProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  (api.getTickets as jest.Mock).mockResolvedValue([]);
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
    project_path: null,
    accepted_frontier_id: 'f'.repeat(40),
    shipped_frontier: 'e'.repeat(40),
  });
  (api.getTickets as jest.Mock).mockResolvedValue([
    {
      id: 'ticket-1',
      project_id: 'proj-1',
      column_id: 'todo',
      title: 'Wire up visibility',
      priority: 'medium',
      status: 'todo',
      intent_status: 'ready',
      display_state: 'stale',
      latest_attempt: {
        id: 'attempt-1',
        short_commit_hash: 'abc1234',
        status: 'accepted',
        wave_num: 2,
        attempt_num: 1,
        summary: 'summary',
        test_status: 'passed',
        accepted_frontier_id: 'f'.repeat(40),
        stale: true,
        stale_reason: 'attempt.base_hash differs from project.accepted_frontier_id.',
      },
      accepted_attempt: null,
    },
    {
      id: 'ticket-2',
      project_id: 'proj-1',
      column_id: 'todo',
      title: 'Keep frontier current',
      priority: 'medium',
      status: 'todo',
      intent_status: 'ready',
      display_state: 'accepted',
      latest_attempt: {
        id: 'attempt-2',
        short_commit_hash: 'def5678',
        status: 'accepted',
        wave_num: 2,
        attempt_num: 2,
        summary: 'summary',
        test_status: 'passed',
        accepted_frontier_id: 'f'.repeat(40),
        stale: false,
        stale_reason: null,
      },
      accepted_attempt: null,
    },
  ]);

  renderProjectPage();

  await waitFor(() => {
    expect(screen.getByText('Wizard')).toBeInTheDocument();
    expect(screen.getByText('Source: GitHub (recommended)')).toBeInTheDocument();
    expect(screen.getByText('GitHub URL: https://github.com/acme/wizard')).toBeInTheDocument();
    expect(screen.getByText('GitHub ref: release/2026.06')).toBeInTheDocument();
    expect(screen.getByText('Source SHA: 1234567890abcdef1234567890abcdef12345678')).toBeInTheDocument();
    expect(screen.getByText('Import to AgentHub: Yes')).toBeInTheDocument();
    expect(screen.getByText('Tickets start from AgentHub frontier. No local project path is configured.')).toBeInTheDocument();
    expect(screen.getByText('AgentHub DAG source of truth')).toBeInTheDocument();
    expect(screen.getByText('No local path mode')).toBeInTheDocument();
    expect(screen.getByText('Accepted frontier')).toBeInTheDocument();
    expect(screen.getByText('Source base')).toBeInTheDocument();
    expect(screen.getByText('Recent ticket attempts')).toBeInTheDocument();
    expect(screen.getByText('Wire up visibility')).toBeInTheDocument();
    expect(screen.getByText('Keep frontier current')).toBeInTheDocument();
    expect(screen.getByText('Stale')).toBeInTheDocument();
    expect(screen.getByText('Current')).toBeInTheDocument();
  });
});
