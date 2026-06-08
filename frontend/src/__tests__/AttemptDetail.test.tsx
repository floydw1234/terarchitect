import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  getProject: jest.fn(),
  getTicketAttempt: jest.fn(),
  getTicketAttempts: jest.fn(),
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
  (api.getTicketAttempt as jest.Mock).mockResolvedValue({
    id: 'attempt-1',
    project_id: 'proj-1',
    ticket_id: 'ticket-1',
    agenthub_commit_hash: 'a'.repeat(40),
    base_hash: 'b'.repeat(40),
    wave_num: 0,
    attempt_num: 2,
    status: 'accepted',
    summary: 'Implemented the thing',
    test_status: 'passed',
    test_output: '1 passed',
    stale: false,
    created_at: null,
    updated_at: null,
  });
});

test('Attempt detail loads a single attempt and renders the inspector surface', async () => {
  renderAttemptDetail();

  await waitFor(() => {
    expect(screen.getByText('Attempt Detail')).toBeInTheDocument();
    expect(screen.getByText('Test Project')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ship Room' })).toHaveAttribute('href', '/projects/proj-1/ship');
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeInTheDocument();
    expect(screen.getByText('Attempt #2')).toBeInTheDocument();
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    expect(screen.queryByText('wave 0')).not.toBeInTheDocument();
    expect(screen.getByText('Commit hash')).toBeInTheDocument();
    expect(screen.getByText('Base hash')).toBeInTheDocument();
    expect(screen.getByText('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')).toBeInTheDocument();
    expect(screen.getByText('bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')).toBeInTheDocument();
    expect(screen.getByText('Summary')).toBeInTheDocument();
    expect(screen.getByText('Implemented the thing')).toBeInTheDocument();
    expect(screen.getByText('Test status')).toBeInTheDocument();
    expect(screen.getByText('passed')).toBeInTheDocument();
    expect(screen.getByText('1 passed')).toBeInTheDocument();
  });

  expect(api.getProject).toHaveBeenCalledWith('proj-1');
  expect(api.getTicketAttempt).toHaveBeenCalledWith('proj-1', 'ticket-1', 'attempt-1', true);
  expect(api.getTicketAttempts).not.toHaveBeenCalled();
});

test('Attempt detail surfaces a loading error when the attempt cannot be found', async () => {
  (api.getTicketAttempt as jest.Mock).mockRejectedValue(new Error('Attempt not found'));

  renderAttemptDetail();

  await waitFor(() => {
    expect(screen.getByRole('alert')).toHaveTextContent('Attempt not found');
  });

  expect(api.getTicketAttempts).not.toHaveBeenCalled();
});
