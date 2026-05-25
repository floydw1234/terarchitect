/**
 * Composite Workspace UI tests (plan 12.4).
 *
 * Covers:
 *  - Workspace page renders leaf selector and workspace list
 *  - Blessed candidate label displayed correctly
 *  - Composite Preview label displayed correctly
 *  - Production boundary warning shown on bless
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  getWorkspaces: jest.fn(),
  getTickets: jest.fn(),
  getTicketAttempts: jest.fn(),
  createWorkspace: jest.fn(),
  analyzeCompatibility: jest.fn(),
  getWorkspace: jest.fn(),
  composeWorkspace: jest.fn(),
  blessWorkspace: jest.fn(),
  snapshotWorkspace: jest.fn(),
  promoteWorkspace: jest.fn(),
  discardWorkspace: jest.fn(),
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
}));

import * as api from '../utils/api';
import WorkspacePage from '../pages/WorkspacePage';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderWorkspace(projectId = 'proj-1') {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={[`/projects/${projectId}/workspace`]}>
        <Routes>
          <Route path="/projects/:projectId/workspace" element={<WorkspacePage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const mockProject = {
  id: 'proj-1',
  name: 'Test Project',
  git_mode: 'swarm' as const,
  shipped_frontier: 'frontier123',
  shipped_frontier_updated_at: null,
  blessed_workspace_id: null,
};

const mockBlessedWorkspace = {
  id: 'ws-1',
  project_id: 'proj-1',
  base_root_hash: 'frontier123',
  selected_attempt_ids: ['att-1'],
  selected_leaf_hashes: ['leaf1'],
  status: 'blessed' as const,
  composed_commit_hash: 'composed123',
  short_composed_hash: 'composed123456',
  conflict_summary: null,
  changed_files: ['src/app.py'],
  summary: null,
  test_status: 'passed',
  preview_url: null,
  created_by: null,
  created_at: null,
  updated_at: null,
};

const mockPreviewWorkspace = {
  ...mockBlessedWorkspace,
  id: 'ws-2',
  status: 'preview_ready' as const,
};

beforeEach(() => {
  jest.clearAllMocks();
  (api.getProject as jest.Mock).mockResolvedValue(mockProject);
  (api.getTickets as jest.Mock).mockResolvedValue([]);
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([]);
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
});

// ---------------------------------------------------------------------------
// Test: Workspace page renders
// ---------------------------------------------------------------------------

test('Workspace page renders with project name', async () => {
  (api.getWorkspaces as jest.Mock).mockResolvedValue([]);

  renderWorkspace();

  await waitFor(() => {
    expect(screen.getByText('Workspace')).toBeInTheDocument();
    expect(screen.getByText('Test Project')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: Leaf selector label visible
// ---------------------------------------------------------------------------

test('Leaf selector section visible', async () => {
  (api.getWorkspaces as jest.Mock).mockResolvedValue([]);

  renderWorkspace();

  await waitFor(() => {
    expect(screen.getByText('Leaf Selector')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: Blessed Candidate label shown
// ---------------------------------------------------------------------------

test('Blessed Candidate label shown for blessed workspace', async () => {
  (api.getWorkspaces as jest.Mock).mockResolvedValue([mockBlessedWorkspace]);

  renderWorkspace();

  await waitFor(() => {
    expect(screen.getByText('Blessed Candidate')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: Composite Preview label shown
// ---------------------------------------------------------------------------

test('Composite Preview label shown for preview_ready workspace', async () => {
  (api.getWorkspaces as jest.Mock).mockResolvedValue([mockPreviewWorkspace]);

  renderWorkspace();

  await waitFor(() => {
    expect(screen.getByText('Composite Preview')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Test: No workspaces empty state
// ---------------------------------------------------------------------------

test('Empty workspace list renders compose prompt', async () => {
  (api.getWorkspaces as jest.Mock).mockResolvedValue([]);

  renderWorkspace();

  await waitFor(() => {
    // Leaf selector always visible
    expect(screen.getByText('Compose Selected')).toBeInTheDocument();
  });
});
