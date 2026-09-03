import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeContextProvider } from '../contexts/ThemeContext';

jest.mock('../utils/api', () => ({
  getKanban: jest.fn(),
  getProject: jest.fn(),
  getTickets: jest.fn(),
  getGraph: jest.fn(),
  createTicket: jest.fn(),
  updateTicket: jest.fn(),
  deleteTicket: jest.fn(),
  getNotes: jest.fn(),
  createNote: jest.fn(),
  updateNote: jest.fn(),
  deleteNote: jest.fn(),
  updateKanban: jest.fn(),
  getTicketLogs: jest.fn(),
  cancelTicketExecution: jest.fn(),
  getExecutionReady: jest.fn(),
  startProject: jest.fn(),
  rerunTicketFromCurrentFrontier: jest.fn(),
  AGENTHUB_URL: 'http://agenthub.local',
  ticketChannelName: jest.fn(() => 'ticket-1234567890abcdef123456'),
}));

import * as api from '../utils/api';
import KanbanPage from '../pages/KanbanPage';

function renderKanbanPage() {
  return render(
    <ThemeContextProvider>
      <MemoryRouter initialEntries={['/projects/proj-1/kanban']}>
        <Routes>
          <Route path="/projects/:projectId/kanban" element={<KanbanPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeContextProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();

  (api.getKanban as jest.Mock).mockResolvedValue({ columns: [] });
  (api.getProject as jest.Mock).mockResolvedValue({
    id: 'proj-1',
    name: 'Test Project',
    git_mode: 'swarm',
    accepted_frontier_id: 'f'.repeat(40),
    shipped_frontier: 'e'.repeat(40),
  });
  (api.getTickets as jest.Mock).mockResolvedValue([
    {
      id: 'ticket-1',
      project_id: 'proj-1',
      column_id: 'backlog',
      title: 'Refresh stale attempt',
      description: 'Retry from the current frontier',
      priority: 'medium',
      status: 'todo',
      intent_status: 'ready',
      display_state: 'stale',
      attempts_count: 1,
      stale: true,
      stale_reason: 'attempt.base_hash differs from project.accepted_frontier_id.',
      depends_on_ticket_ids: [],
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
  ]);
  (api.getNotes as jest.Mock).mockResolvedValue([]);
  (api.getGraph as jest.Mock).mockResolvedValue({
    nodes: [{ id: 'node-1', data: { label: 'Node 1' } }],
    edges: [],
  });
  (api.getExecutionReady as jest.Mock).mockResolvedValue({
    missing: [],
    features: { composite_workspace: false },
  });
  (api.rerunTicketFromCurrentFrontier as jest.Mock).mockResolvedValue({
    id: 'ticket-1',
    project_id: 'proj-1',
    column_id: 'backlog',
    title: 'Refresh stale attempt',
    priority: 'medium',
    status: 'todo',
    intent_status: 'ready',
    display_state: 'stale',
    attempts_count: 3,
    stale: true,
    stale_reason: 'attempt.base_hash differs from project.accepted_frontier_id.',
    depends_on_ticket_ids: [],
    latest_attempt: {
      id: 'attempt-2',
      short_commit_hash: 'def5678',
      status: 'queued',
      wave_num: 2,
      attempt_num: 3,
      summary: 'queued',
      test_status: null,
      accepted_frontier_id: 'f'.repeat(40),
      stale: false,
      stale_reason: null,
    },
    accepted_attempt: null,
  });
});

test('stale ticket card can launch competing attempts from the current frontier', async () => {
  const user = userEvent.setup();

  renderKanbanPage();

  await waitFor(() => {
    expect(screen.getByText('Refresh stale attempt')).toBeInTheDocument();
  });

  await user.click(screen.getByRole('button', { name: 'Run competing attempts' }));

  expect(screen.getByRole('dialog')).toBeInTheDocument();
  expect(screen.getByText('Start 2 or 3 fresh attempts from the current frontier for this ticket.')).toBeInTheDocument();

  await user.click(screen.getByRole('button', { name: 'Start 3 attempts' }));

  await waitFor(() => {
    expect(api.rerunTicketFromCurrentFrontier).toHaveBeenCalledWith('proj-1', 'ticket-1', { attemptCount: 3 });
  });

  expect(screen.getByText('Queued 3 competing attempts from current frontier.')).toBeInTheDocument();
});

test('competing attempts dialog surfaces rerun failures', async () => {
  const user = userEvent.setup();
  (api.rerunTicketFromCurrentFrontier as jest.Mock).mockRejectedValueOnce(new Error('API 409: frontier moved'));

  renderKanbanPage();

  await waitFor(() => {
    expect(screen.getByText('Refresh stale attempt')).toBeInTheDocument();
  });

  await user.click(screen.getByRole('button', { name: 'Run competing attempts' }));
  await user.click(screen.getByRole('button', { name: 'Start 2 attempts' }));

  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('API 409: frontier moved');
  });
});
