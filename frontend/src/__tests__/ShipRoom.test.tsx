import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  getShipCandidates: jest.fn(),
  getShipCandidateDetail: jest.fn(),
  getTicketAttempts: jest.fn(),
  composeShipCandidate: jest.fn(),
  shipCandidate: jest.fn(),
  sendCandidateFeedback: jest.fn(),
  dryComposeShipCandidate: jest.fn(),
  getCandidateDiff: jest.fn(),
  getCandidateTimeline: jest.fn(),
  acceptAttempt: jest.fn(),
  rejectAttempt: jest.fn(),
  rerunTicketFromCurrentFrontier: jest.fn(),
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
  accepted_frontier_id: 'leaf_current_frontier_0123456789abcdef',
  shipped_frontier: 'abc123def4567890',
  shipped_frontier_updated_at: null,
};

const mockReadyToShipRun = {
  id: 'run-1',
  project_id: 'proj-1',
  promotion_candidate_id: 'candidate-1',
  status: 'ready_to_ship',
  error: null,
  release_branch: 'terarchitect/release/candidate-abc',
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
  candidate: null,
  membership: null,
  validation_errors: [],
  tickets: [],
  commit_hashes: [],
};

const mockComposeFailedRun = {
  ...mockReadyToShipRun,
  id: 'run-2',
  promotion_candidate_id: 'candidate-2',
  status: 'compose_failed',
  error: 'Merge conflict in src/models.py',
  release_branch: null,
  base_main_hash: null,
  composed_commit_hash: null,
  release_pr_url: null,
  release_pr_number: null,
};

const mockShippedRun = {
  ...mockReadyToShipRun,
  id: 'run-3',
  promotion_candidate_id: 'candidate-3',
  status: 'shipped',
  release_branch: 'terarchitect/release/candidate-def',
  base_main_hash: 'base456',
  composed_commit_hash: 'composed456',
  changed_files: ['src/lib.py'],
  release_pr_url: 'https://github.com/o/r/pull/43',
  release_pr_number: 43,
  shipped_at: '2026-06-08T12:00:00Z',
  shipped_commit_hash: 'shipped456',
};

const mockAcceptedAttempt = {
  id: 'attempt-1',
  project_id: 'proj-1',
  ticket_id: 'ticket-1',
  agenthub_commit_hash: 'a'.repeat(40),
  short_commit_hash: 'aaaaaaaaaaaa',
  base_hash: 'b'.repeat(40),
  attempt_num: 1,
  agent_id: 'agent-1',
  status: 'accepted',
  summary: 'attempt summary',
  validation_error: null,
  test_status: 'passed',
  test_output: null,
  stale: false,
  accepted_frontier_id: mockProject.accepted_frontier_id,
  base_leaf_id: 'b'.repeat(40),
  parent_leaf_id: 'b'.repeat(40),
  stale_reason: null,
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
  attempt_num: 2,
  agent_id: 'agent-2',
  status: 'proposed',
  summary: 'needs human review',
  validation_error: null,
  test_status: 'failed',
  test_output: null,
  stale: false,
  accepted_frontier_id: mockProject.accepted_frontier_id,
  base_leaf_id: 'c'.repeat(40),
  parent_leaf_id: 'c'.repeat(40),
  stale_reason: null,
  created_at: null,
  updated_at: null,
};

const baseCandidate = {
  id: 'candidate-1',
  project_id: 'proj-1',
  selected_attempt_ids: ['attempt-1'],
  selected_leaf_hashes: ['a'.repeat(40)],
  base_root_hash: mockProject.shipped_frontier,
  status: 'ready',
  validation_summary: {},
  conflict_summary: null,
  composed_commit_hash: null,
  created_at: null,
  updated_at: null,
};

function makeCandidateDetail(overrides: Partial<any> = {}) {
  return {
    ...baseCandidate,
    membership: {
      attempts: [mockAcceptedAttempt],
      tickets: [{ id: 'ticket-1', title: 'Ticket Alpha', column_id: 'done', depends_on_ticket_ids: [] }],
      commit_hashes: ['a'.repeat(40)],
    },
    validation_errors: [],
    latest_ship_run: null,
    ...overrides,
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  (api.getProject as jest.Mock).mockResolvedValue(mockProject);
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([]);
  (api.dryComposeShipCandidate as jest.Mock).mockResolvedValue({
    candidate_id: 'candidate-1',
    safe_to_compose: true,
    blockers: [],
    next_actions: ['Compose this promotion candidate when you want a release-branch preview.'],
    shipped_frontier: mockProject.shipped_frontier,
    commit_hashes: [],
    changed_files: [],
    existing_ship_run: null,
    tickets: [],
  });
  (api.getCandidateDiff as jest.Mock).mockResolvedValue({
    candidate_id: 'candidate-1',
    base_hash: 'base123',
    composed_commit_hash: 'composed123',
    changed_files: ['src/app.py'],
    diff: ' src/app.py | 2 ++',
    truncated: false,
    note: null,
    next_actions: [],
    blockers: [],
  });
  (api.getCandidateTimeline as jest.Mock).mockResolvedValue([]);
});

test('Ship Room header shows the accepted frontier hash', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Ship Room')).toBeInTheDocument();
    expect(screen.getByText('Accepted AgentHub frontier')).toBeInTheDocument();
    expect(screen.getByText('leaf_current')).toBeInTheDocument();
  });
});

test('stale attempts show lineage and rerun affordance', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([baseCandidate]);
  (api.getShipCandidateDetail as jest.Mock).mockResolvedValue(makeCandidateDetail({
    membership: {
      attempts: [{ ...mockAcceptedAttempt, stale: true, stale_reason: 'attempt.base_hash differs from project.accepted_frontier_id.' }],
      tickets: [{ id: 'ticket-1', title: 'Ticket Alpha', column_id: 'done', depends_on_ticket_ids: [] }],
      commit_hashes: ['a'.repeat(40)],
    },
  }));
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([
    { ...mockAcceptedAttempt, stale: true, stale_reason: 'attempt.base_hash differs from project.accepted_frontier_id.' },
  ]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/attempt.base_hash differs from project.accepted_frontier_id/i).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Rerun from frontier' })).toBeInTheDocument();
    expect(screen.getAllByText('base').length).toBeGreaterThan(0);
    expect(screen.getAllByText('parent').length).toBeGreaterThan(0);
  });
});

test('ready_to_ship run shows the release PR at ShipRun level', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([baseCandidate]);
  (api.getShipCandidateDetail as jest.Mock).mockResolvedValue(makeCandidateDetail({
    latest_ship_run: mockReadyToShipRun,
  }));

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/Candidate 1/i).length).toBeGreaterThan(0);
    expect(screen.getAllByRole('link', { name: /Release PR #42/i }).length).toBeGreaterThan(0);
    expect(screen.getByText('Compose preview')).toBeInTheDocument();
    expect(screen.getByText('Safe to compose this candidate.')).toBeInTheDocument();
    expect(screen.getByText('Composed diff')).toBeInTheDocument();
  });

  fireEvent.click(screen.getAllByText(/Candidate 1/i).at(-1)!);

  await waitFor(() => {
    const prLink = screen.getAllByRole('link', { name: /Release PR #42/i })[0];
    expect(prLink).toHaveAttribute('href', mockReadyToShipRun.release_pr_url);
    expect(screen.getAllByText('Ready to Ship').length).toBeGreaterThan(0);
  });
});

test('compose_failed state shows its error text', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([{ ...baseCandidate, id: 'candidate-2' }]);
  (api.getShipCandidateDetail as jest.Mock).mockResolvedValue(makeCandidateDetail({
    id: 'candidate-2',
    latest_ship_run: mockComposeFailedRun,
  }));

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/Candidate 2/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText('Merge conflict in src/models.py').length).toBeGreaterThan(0);
  });

  fireEvent.click(screen.getAllByText(/Candidate 2/i).at(-1)!);

  await waitFor(() => {
    expect(screen.getAllByText('Compose Failed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Merge conflict in src/models.py').length).toBeGreaterThan(0);
  });
});

test('shipped state is visually distinct from accepted state', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([
    baseCandidate,
    { ...baseCandidate, id: 'candidate-3', selected_attempt_ids: ['attempt-1'] },
  ]);
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockAcceptedAttempt]);
  (api.getShipCandidateDetail as jest.Mock)
    .mockResolvedValueOnce(makeCandidateDetail())
    .mockResolvedValueOnce(makeCandidateDetail({
      id: 'candidate-3',
      membership: {
        attempts: [mockAcceptedAttempt],
        tickets: [{ id: 'ticket-1', title: 'Ticket Alpha', column_id: 'done', depends_on_ticket_ids: [] }],
        commit_hashes: ['a'.repeat(40)],
      },
      latest_ship_run: mockShippedRun,
    }));

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/Candidate/i).length).toBeGreaterThanOrEqual(2);
  });

  fireEvent.click(screen.getAllByText(/Candidate 3/i).at(-1)!);

  await waitFor(() => {
    expect(screen.getByText(/Shipped · shipped456/)).toBeInTheDocument();
    const ticketCard = screen.getByTestId('ticket-card-ticket-1');
    expect(within(ticketCard).getByText('Accepted · aaaaaaaaaaaa')).toBeInTheDocument();
    expect(screen.getByText('Accepted attempts (1)')).toBeInTheDocument();
  });
});

test('Ship Room review controls can accept a proposed attempt', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([baseCandidate]);
  (api.getShipCandidateDetail as jest.Mock).mockResolvedValue(makeCandidateDetail({
    membership: {
      attempts: [],
      tickets: [{ id: 'ticket-1', title: 'Ticket Alpha', column_id: 'done', depends_on_ticket_ids: [] }],
      commit_hashes: [],
    },
  }));
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockProposedAttempt, mockAcceptedAttempt]);
  (api.acceptAttempt as jest.Mock).mockResolvedValue({ ...mockProposedAttempt, status: 'accepted' });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/Candidate 1/i).length).toBeGreaterThan(0);
  });

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
  (api.getShipCandidates as jest.Mock).mockResolvedValue([baseCandidate]);
  (api.getShipCandidateDetail as jest.Mock).mockResolvedValue(makeCandidateDetail({
    membership: {
      attempts: [],
      tickets: [{ id: 'ticket-1', title: 'Ticket Alpha', column_id: 'done', depends_on_ticket_ids: [] }],
      commit_hashes: [],
    },
  }));
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockProposedAttempt]);
  (api.rejectAttempt as jest.Mock).mockResolvedValue({ ...mockProposedAttempt, status: 'rejected' });

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/Candidate 1/i).length).toBeGreaterThan(0);
  });

  fireEvent.click(screen.getAllByText(/Candidate 1/i).at(-1)!);

  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Reject attempt #2' })).toBeEnabled();
  });

  fireEvent.click(screen.getByRole('button', { name: 'Reject attempt #2' }));

  await waitFor(() => {
    expect(api.rejectAttempt).toHaveBeenCalledWith('proj-1', 'ticket-1', 'attempt-2', 'Rejected from Ship Room review.');
  });
});

test('locked attempts disable review actions with a useful explanation', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([baseCandidate]);
  (api.getShipCandidateDetail as jest.Mock).mockResolvedValue(makeCandidateDetail({
    latest_ship_run: mockReadyToShipRun,
  }));
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockAcceptedAttempt]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/Candidate 1/i).length).toBeGreaterThan(0);
  });

  fireEvent.click(screen.getAllByText(/Candidate 1/i).at(-1)!);

  await waitFor(() => {
    const acceptButton = screen.getByRole('button', { name: 'Accept attempt #1' });
    const rejectButton = screen.getByRole('button', { name: 'Reject attempt #1' });
    expect(acceptButton).toBeDisabled();
    expect(rejectButton).toBeDisabled();
    expect(screen.getByText(/attempt review is locked/i)).toBeInTheDocument();
  });
});

test('ticket rows do not show PR language', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([baseCandidate]);
  (api.getShipCandidateDetail as jest.Mock).mockResolvedValue(makeCandidateDetail({
    latest_ship_run: mockReadyToShipRun,
  }));
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([mockAcceptedAttempt]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getAllByText(/Candidate 1/i).length).toBeGreaterThan(0);
  });

  fireEvent.click(screen.getAllByText(/Candidate 1/i).at(-1)!);

  await waitFor(() => {
    const ticketCard = screen.getByTestId('ticket-card-ticket-1');
    expect(within(ticketCard).queryByRole('link', { name: /PR/i })).not.toBeInTheDocument();
    expect(within(ticketCard).queryByText(/ticket PR/i)).not.toBeInTheDocument();
  });
});

test('empty state shown when no candidates exist', async () => {
  (api.getShipCandidates as jest.Mock).mockResolvedValue([]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText(/No promotion candidates yet/)).toBeInTheDocument();
  });
});
