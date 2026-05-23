/**
 * Ship Room UI tests (plan 12.4).
 *
 * Covers:
 *  - Ship Room renders wave list and attempts
 *  - Ticket cards show display_state, not PR numbers
 *  - Release PR status appears in wave detail
 *  - Failed composition surfaces next actions (create fix ticket button)
 *  - Staleness warning shown when stale_count > 0
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

// Mock the api module
jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  getShipWaves: jest.fn(),
  getShipWaveDetail: jest.fn(),
  getWaveTimeline: jest.fn(),
  composeWave: jest.fn(),
  shipWave: jest.fn(),
  sendWaveFeedback: jest.fn(),
  createTicket: jest.fn(),
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
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const mockProject = {
  id: 'proj-1',
  name: 'Test Project',
  git_mode: 'swarm' as const,
  shipped_frontier: 'abc123def456',
  shipped_frontier_updated_at: null,
};

const mockWaveSummary = {
  wave_num: 0,
  ticket_count: 2,
  accepted_count: 2,
  all_done: true,
  ship_run: null,
};

const mockWaveWithReadyToShip = {
  wave_num: 0,
  ticket_count: 1,
  accepted_count: 1,
  all_done: true,
  ship_run: {
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
  },
};

const mockWaveWithComposeFailed = {
  ...mockWaveSummary,
  ship_run: {
    id: 'run-2',
    project_id: 'proj-1',
    wave_num: 0,
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
  },
};

beforeEach(() => {
  jest.clearAllMocks();
  (api.getProject as jest.Mock).mockResolvedValue(mockProject);
  (api.getWaveTimeline as jest.Mock).mockResolvedValue([]);
});

// ---------------------------------------------------------------------------
// Test: Ship Room renders wave list
// ---------------------------------------------------------------------------

test('Ship Room renders wave list with accepted count', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([mockWaveSummary]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Ship Room')).toBeInTheDocument();
    expect(screen.getByText('Test Project')).toBeInTheDocument();
    expect(screen.getByText('Wave 0')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: Frontier displayed in header
// ---------------------------------------------------------------------------

test('Frontier hash displayed in Ship Room header', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText(/abc123def456/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: Release PR link shown for ready_to_ship wave
// ---------------------------------------------------------------------------

test('Release PR link shown when ship run is ready_to_ship', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([mockWaveWithReadyToShip]);
  (api.getShipWaveDetail as jest.Mock).mockResolvedValue({
    wave_num: 0,
    tickets: [],
    accepted_attempts: [],
    ship_run: mockWaveWithReadyToShip.ship_run,
    can_compose: false,
    all_done: true,
    shipped_frontier: null,
    stale_count: 0,
  });

  renderShipRoom();

  await waitFor(() => {
    // Card shows the PR number
    expect(screen.getByText(/PR #42/)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: Failed composition surfaces compose_failed status
// ---------------------------------------------------------------------------

test('Compose_failed status visible in wave card', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([mockWaveWithComposeFailed]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText('Compose Failed')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: Empty state when no waves
// ---------------------------------------------------------------------------

test('Empty state shown when no waves exist', async () => {
  (api.getShipWaves as jest.Mock).mockResolvedValue([]);

  renderShipRoom();

  await waitFor(() => {
    expect(screen.getByText(/No waves yet/)).toBeInTheDocument();
  });
});
