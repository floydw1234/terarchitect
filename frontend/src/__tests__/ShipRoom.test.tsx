/**
 * Ship Room MVP UI tests.
 *
 * Encodes the Phase 4 contract:
 * - frontier appears in the header
 * - ready_to_ship run exposes the release PR at ShipRun level
 * - compose_failed surfaces its error text
 * - shipped state is distinct from accepted state
 * - wave cards and ticket rows do not show PR language
 */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  getShipWaves: jest.fn(),
  getShipWaveDetail: jest.fn(),
  getWaveTimeline: jest.fn(),
  getTicketAttempts: jest.fn(),
  composeWave: jest.fn(),
  shipWave: jest.fn(),
  sendWaveFeedback: jest.fn(),
  acceptAttempt: jest.fn(),
  rejectAttempt: jest.fn(),
  createTicket: jest.fn(),
  getEvidencePolicy: jest.fn(),
  getEvidence: jest.fn(),
  getEvidenceRuns: jest.fn(),
  collectEvidence: jest.fn(),
  runCommandEvidence: jest.fn(),
  queueEvidenceRun: jest.fn(),
  compareEvidence: jest.fn(),
  addEvidenceWaiver: jest.fn(),
  addEvidenceApproval: jest.fn(),
  createEvidenceRepairTicket: jest.fn(),
  rerunEvidenceChecks: jest.fn(),
  runEvidenceSuite: jest.fn(),
  AGENTHUB_URL: '',
  ticketChannelName: (id: string) => `ticket-${id.replace(/-/g, '').slice(0, 24)}`,
}));

import * as api from '../utils/api';
import ShipRoomPage from '../pages/ShipRoomPage';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderShipRoom(projectId = 'proj-1') {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[`/projects/${projectId}/ship`]}>
        <Routes>
          <Route path="/projects/:projectId/ship" element={<ShipRoomPage />} />
          <Route path="/projects/:projectId/tickets/:ticketId/attempts/:attemptId" element={<div>Attempt detail route</div>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const mockProject = {
  id: 'proj-1',
  name: 'Test Project',
  git_mode: 'swarm' as const,
  shipped_frontier: 'abc123def4567890',
  shipped_frontier_updated_at: null,
};

const mockReadyToShipRun = {
  id: 'run-1',
  project_id: 'proj-1',
  wave_num: 0,
  status: 'ready_to_ship',
  error: null,
  release_branch: 'terarchitect/release/wave-0-abc',
  base_main_hash: 'base123',
  composed_commit_hash: 'composed123',
  changed_files: ['src/app.py', 'tests/test_app.py'],
  summary: null,
  test_status: 'passed',
  test_output: null,
  release_pr_url: 'https://github.com/o/r/pull/42',
  release_pr_number: 42,
  shipped_at: null,
  shipped_commit_hash: null,
  created_at: null,
  updated_at: null,
};

const mockComposeFailedRun = {
  id: 'run-2',
  project_id: 'proj-1',
  wave_num: 1,
  status: 'compose_failed',
  error: 'Merge conflict in src/models.py',
  release_branch: null,
  base_main_hash: null,
  composed_commit_hash: null,
  changed_files: [],
  summary: null,
  test_status: null,
  test_output: null,
  release_pr_url: null,
  release_pr_number: null,
  shipped_at: null,
  shipped_commit_hash: null,
  created_at: null,
  updated_at: null,
};

const mockShippedRun = {
  id: 'run-3',
  project_id: 'proj-1',
  wave_num: 2,
  status: 'shipped',
  error: null,
  release_branch: 'terarchitect/release/wave-2-def',
  base_main_hash: 'base456',
  composed_commit_hash: 'composed456',
  changed_files: ['src/lib.py'],
  summary: null,
  test_status: 'passed',
  test_output: null,
  release_pr_url: 'https://github.com/o/r/pull/43',
  release_pr_number: 43,
  shipped_at: '2026-06-08T12:00:00Z',
  shipped_commit_hash: 'shipped456',
  created_at: null,
  updated_at: null,
};

const mockAcceptedAttempt = {
  id: 'attempt-1',
  project_id: 'proj-1',
  ticket_id: 'ticket-1',
  agenthub_commit_hash: 'a'.repeat(40),
  short_commit_hash: 'aaaaaaaaaaaa',
  base_hash: 'b'.repeat(40),
  wave_num: 0,
  attempt_num: 1,
  agent_id: 'agent-1',
  status: 'accepted',
  summary: 'attempt summary',
  validation_error: null,
  test_status: 'passed',
  test_output: null,
  stale: false,
  created_at: null,
  updated_at: null,
};

const mockProposedAttempt = {
  id: 'attempt-2',
  project_id: 'proj-1',
  ticket_id: 'ticket-1',
  agenthub_commit_hash: 'b'.repeat(40),
  short_commit_hash: 'bbbbbbbbbbbb',
  base_hash: 'c'.repeat(40),
  wave_num: 0,
  attempt_num: 2,
  agent_id: 'agent-2',
  status: 'proposed',
  summary: 'needs human review',
  validation_error: null,
  test_status: 'failed',
  test_output: null,
  stale: false,
  created_at: null,
  updated_at: null,
};

const mockTicket = {
  id: 'ticket-1',
  project_id: 'proj-1',
  column_id: 'done',
  title: 'Ticket Alpha',
  priority: 'medium',
  status: 'ready',
  intent_status: 'ready',
  display_state: 'accepted',
  latest_attempt: {
    id: 'attempt-1',
    short_commit_hash: 'aaaaaaaaaaaa',
    status: 'accepted',
    wave_num: 0,
    attempt_num: 1,
    summary: 'attempt summary',
    test_status: 'passed',
    stale: false,
  },
  accepted_attempt: null,
};

const mockReviewTicket = {
  ...mockTicket,
  latest_attempt: {
    ...mockTicket.latest_attempt,
    id: mockProposedAttempt.id,
    short_commit_hash: mockProposedAttempt.short_commit_hash,
    status: mockProposedAttempt.status,
    attempt_num: mockProposedAttempt.attempt_num,
    summary: mockProposedAttempt.summary,
    test_status: mockProposedAttempt.test_status,
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  (api.getProject as jest.Mock).mockResolvedValue(mockProject);
  (api.getWaveTimeline as jest.Mock).mockResolvedValue([]);
  (api.getEvidencePolicy as jest.Mock).mockResolvedValue({
    allowed: true,
    policy: { required_checks: [], optional_checks: [], required_llm_reviewers: [], block_on: [], check_suites: [] },
    bundle: null,
    required_checks: {},
    human_approval: null,
    reasons: [],
  });
  (api.getEvidence as jest.Mock).mockResolvedValue([]);
  (api.getEvidenceRuns as jest.Mock).mockResolvedValue([]);
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([]);
});

test('Ship Room header shows the frontier hash', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Ship Room')).toBeInTheDocument();
    expect(screen.getByText('Current shipped frontier')).toBeInTheDocument();
    expect(screen.getByText(/abc123def456/)).toBeInTheDocument();
  });
});

test('ready_to_ship run shows the release PR at ShipRun level', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([{
    wave_num: 0,
    ticket_count: 1,
    accepted_count: 1,
    all_done: true,
    ship_run: mockReadyToShipRun,
  }]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 0,
    tickets: [],
    accepted_attempts: [],
    ship_run: mockReadyToShipRun,
    can_compose: false,
    all_done: true,
    shipped_frontier: mockProject.shipped_frontier,
    stale_count: 0,
  });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Wave 0')).toBeInTheDocument();
    expect(screen.queryByText(/PR #42/i)).not.toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Wave 0'));

  await waitFor(() => {
    const prLink = screen.getByRole('link', { name: /Release PR #42/i });
    expect(prLink).toHaveAttribute('href', mockReadyToShipRun.release_pr_url);
    expect(screen.getAllByText('Ready to Ship').length).toBeGreaterThan(0);
  });
});

test('compose_failed state shows its error text', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([{
    wave_num: 1,
    ticket_count: 1,
    accepted_count: 1,
    all_done: true,
    ship_run: mockComposeFailedRun,
  }]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 1,
    tickets: [],
    accepted_attempts: [],
    ship_run: mockComposeFailedRun,
    can_compose: false,
    all_done: true,
    shipped_frontier: mockProject.shipped_frontier,
    stale_count: 0,
  });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Wave 1')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Wave 1'));

  await waitFor(() => {
    expect(screen.getAllByText('Compose Failed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Merge conflict in src/models.py').length).toBeGreaterThan(0);
  });
});

test('shipped state is visually distinct from accepted state', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([
    {
      wave_num: 0,
      ticket_count: 1,
      accepted_count: 1,
      all_done: true,
      ship_run: null,
    },
    {
      wave_num: 2,
      ticket_count: 1,
      accepted_count: 1,
      all_done: true,
      ship_run: mockShippedRun,
    },
  ]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 2,
    tickets: [mockTicket],
    accepted_attempts: [mockAcceptedAttempt],
    ship_run: mockShippedRun,
    can_compose: false,
    all_done: true,
    shipped_frontier: mockProject.shipped_frontier,
    stale_count: 0,
  });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Wave 2')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Wave 2'));

  await waitFor(() => {
    expect(screen.getByText(/Shipped · shipped45/)).toBeInTheDocument();
    const ticketCard = screen.getByTestId('ticket-card-ticket-1');
    expect(within(ticketCard).getByText('accepted · aaaaaaaaaaaa')).toBeInTheDocument();
    expect(screen.getByText('Accepted attempts (1)')).toBeInTheDocument();
  });
});

test('Ship Room review controls can accept a proposed attempt', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([{
    wave_num: 0,
    ticket_count: 1,
    accepted_count: 0,
    all_done: false,
    ship_run: null,
  }]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 0,
    tickets: [mockReviewTicket],
    accepted_attempts: [],
    ship_run: null,
    can_compose: false,
    all_done: false,
    shipped_frontier: mockProject.shipped_frontier,
    stale_count: 0,
  });
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockProposedAttempt, mockAcceptedAttempt]);
  (api.acceptAttempt as jest.Mock).mockResolvedValue({ ...mockProposedAttempt, status: 'accepted' });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Wave 0')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Wave 0'));

  await waitFor(() => {
    expect(screen.getByText('Ticket Alpha')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept attempt #2' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Reject attempt #2' })).toBeEnabled();
  });

  fireEvent.click(screen.getByRole('button', { name: 'Accept attempt #2' }));

  await waitFor(() => {
    expect(api.acceptAttempt).toHaveBeenCalledWith('proj-1', 'ticket-1', 'attempt-2');
  });
});

test('Ship Room review controls can reject a proposed attempt', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([{
    wave_num: 0,
    ticket_count: 1,
    accepted_count: 0,
    all_done: false,
    ship_run: null,
  }]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 0,
    tickets: [mockReviewTicket],
    accepted_attempts: [],
    ship_run: null,
    can_compose: false,
    all_done: false,
    shipped_frontier: mockProject.shipped_frontier,
    stale_count: 0,
  });
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockProposedAttempt]);
  (api.rejectAttempt as jest.Mock).mockResolvedValue({ ...mockProposedAttempt, status: 'rejected' });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Wave 0')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Wave 0'));

  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Reject attempt #2' })).toBeEnabled();
  });

  fireEvent.click(screen.getByRole('button', { name: 'Reject attempt #2' }));

  await waitFor(() => {
    expect(api.rejectAttempt).toHaveBeenCalledWith('proj-1', 'ticket-1', 'attempt-2', 'Rejected from Ship Room review.');
  });
});

test('locked attempts disable review actions with a useful explanation', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([{
    wave_num: 0,
    ticket_count: 1,
    accepted_count: 1,
    all_done: false,
    ship_run: null,
  }]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 0,
    tickets: [mockTicket],
    accepted_attempts: [],
    ship_run: mockReadyToShipRun,
    can_compose: false,
    all_done: false,
    shipped_frontier: mockProject.shipped_frontier,
    stale_count: 0,
  });
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockAcceptedAttempt]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Wave 0')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Wave 0'));

  await waitFor(() => {
    const acceptButton = screen.getByRole('button', { name: 'Accept attempt #1' });
    const rejectButton = screen.getByRole('button', { name: 'Reject attempt #1' });
    expect(acceptButton).toBeDisabled();
    expect(rejectButton).toBeDisabled();
    expect(screen.getByText(/attempt review is locked/i)).toBeInTheDocument();
  });
});

test('ticket rows do not show PR language', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([{
    wave_num: 0,
    ticket_count: 1,
    accepted_count: 1,
    all_done: true,
    ship_run: mockReadyToShipRun,
  }]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 0,
    tickets: [mockTicket],
    accepted_attempts: [mockAcceptedAttempt],
    ship_run: mockReadyToShipRun,
    can_compose: false,
    all_done: true,
    shipped_frontier: mockProject.shipped_frontier,
    stale_count: 0,
  });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Wave 0')).toBeInTheDocument();
  });
  fireEvent.click(screen.getByText('Wave 0'));

  await waitFor(() => {
    const ticketCard = screen.getByTestId('ticket-card-ticket-1');
    expect(within(ticketCard).queryByRole('link', { name: /PR/i })).not.toBeInTheDocument();
    expect(within(ticketCard).queryByText(/PR/i)).not.toBeInTheDocument();
  });
});

test('empty state shown when no waves exist', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText(/No waves yet/)).toBeInTheDocument();
  });
});
