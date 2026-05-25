import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  getTicketAttempts: jest.fn(),
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
import AttemptDetailPage from '../pages/AttemptDetailPage';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderAttemptDetail() {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter initialEntries={['/projects/proj-1/tickets/ticket-1/attempts/attempt-1']}>
        <Routes>
          <Route path="/projects/:projectId/tickets/:ticketId/attempts/:attemptId" element={<AttemptDetailPage />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  (api.getProject as jest.Mock).mockResolvedValue({ id: 'proj-1', name: 'Test Project' });
  (api.getTicketAttempts as jest.Mock).mockResolvedValue([
    {
      id: 'attempt-1',
      project_id: 'proj-1',
      ticket_id: 'ticket-1',
      agenthub_commit_hash: 'a'.repeat(40),
      short_commit_hash: 'aaaaaaaaaaaa',
      base_hash: 'b'.repeat(40),
      wave_num: 0,
      attempt_num: 2,
      agent_id: 'agent-1',
      status: 'accepted',
      summary: 'Implemented the thing',
      validation_error: null,
      test_status: 'passed',
      test_output: '1 passed',
      stale: false,
      created_at: null,
      updated_at: null,
    },
  ]);
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

test('Attempt detail renders attempt metadata and evidence controls', async () => {
  renderAttemptDetail();

  await waitFor(() => {
    expect(screen.getByText('Attempt Detail')).toBeInTheDocument();
    expect(screen.getByText('Attempt #2')).toBeInTheDocument();
    expect(screen.getByText('Implemented the thing')).toBeInTheDocument();
    expect(screen.getByText('Evidence')).toBeInTheDocument();
  });

  expect(api.getTicketAttempts).toHaveBeenCalledWith('proj-1', 'ticket-1', true);
});
