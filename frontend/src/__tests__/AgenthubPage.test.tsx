import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  AGENTHUB_URL: 'http://agenthub.local',
}));

import AgenthubPage from '../pages/AgenthubPage';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderAgenthubPage() {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <AgenthubPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

function jsonResponse(data: unknown) {
  return Promise.resolve({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => data,
  });
}

function statusResponse(status: number, statusText: string) {
  return Promise.resolve({
    ok: false,
    status,
    statusText,
    json: async () => ({}),
  });
}

describe('AgenthubPage', () => {
  const fetchMock = jest.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    (global as any).fetch = fetchMock;
    window.localStorage.clear();
    delete (window as any).__AH_KEY__;
  });

  test('renders the commit DAG and frontier information from recent commit data', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith('/api/health')) {
        return Promise.resolve({ ok: true, status: 200, statusText: 'OK' });
      }
      if (url.endsWith('/api/git/leaves')) {
        return jsonResponse([
          {
            hash: 'bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222',
            parent_hash: 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
            agent_id: 'agent-beta',
            message: 'Finalize orchestrator pass',
            created_at: '2026-06-17T10:05:00.000Z',
          },
        ]);
      }
      if (url.endsWith('/api/git/commits?limit=30')) {
        return jsonResponse([
          {
            hash: 'bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222',
            parent_hash: 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
            agent_id: 'agent-beta',
            message: 'Finalize orchestrator pass',
            created_at: '2026-06-17T10:05:00.000Z',
          },
          {
            hash: 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
            parent_hash: '',
            agent_id: 'agent-alpha',
            message: 'Seed architecture graph',
            created_at: '2026-06-17T09:55:00.000Z',
          },
        ]);
      }
      if (url.endsWith('/api/channels')) {
        return jsonResponse([
          { id: 1, name: 'ops', description: 'Operator sync', created_at: '2026-06-17T09:30:00.000Z' },
        ]);
      }
      if (url.endsWith('/api/channels/ops/posts?limit=5')) {
        return jsonResponse([
          {
            id: 99,
            channel_id: 1,
            agent_id: 'agent-beta',
            parent_id: null,
            content: 'Graph refresh deployed.',
            created_at: '2026-06-17T10:06:00.000Z',
          },
        ]);
      }

      throw new Error(`Unhandled fetch URL: ${url}`);
    });

    renderAgenthubPage();

    expect(await screen.findByText('Commit DAG')).toBeInTheDocument();
    expect(screen.getAllByText('Finalize orchestrator pass').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Seed architecture graph').length).toBeGreaterThan(0);
    expect(screen.getByText('Frontier')).toBeInTheDocument();
    expect(screen.getByTestId('commit-dag-node-bbbb2222bb')).toBeInTheDocument();
    expect(screen.getByText('#ops')).toBeInTheDocument();
    expect(screen.getByText('Graph refresh deployed.')).toBeInTheDocument();
  });

  test('sends the saved AgentHub key from localStorage', async () => {
    window.localStorage.setItem('terarchitect.agenthub.key', 'saved-secret');

    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);

      if (url.endsWith('/api/health')) {
        return Promise.resolve({ ok: true, status: 200, statusText: 'OK' });
      }
      if (url.endsWith('/api/git/leaves')) {
        expect(init?.headers).toEqual({ Authorization: 'Bearer saved-secret' });
        return jsonResponse([]);
      }
      if (url.endsWith('/api/git/commits?limit=30')) {
        expect(init?.headers).toEqual({ Authorization: 'Bearer saved-secret' });
        return jsonResponse([]);
      }
      if (url.endsWith('/api/channels')) {
        expect(init?.headers).toEqual({ Authorization: 'Bearer saved-secret' });
        return jsonResponse([]);
      }

      throw new Error(`Unhandled fetch URL: ${url}`);
    });

    renderAgenthubPage();

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        'http://agenthub.local/api/git/leaves',
        expect.objectContaining({ headers: { Authorization: 'Bearer saved-secret' } }),
      );
    });
  });

  test('shows the auth helper when AgentHub returns 401', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);

      if (url.endsWith('/api/health')) {
        return Promise.resolve({ ok: true, status: 200, statusText: 'OK' });
      }
      if (
        url.endsWith('/api/git/leaves') ||
        url.endsWith('/api/git/commits?limit=30') ||
        url.endsWith('/api/channels')
      ) {
        return statusResponse(401, 'Unauthorized');
      }

      throw new Error(`Unhandled fetch URL: ${url}`);
    });

    renderAgenthubPage();

    expect(
      await screen.findByText(/AgentHub requires an API key\. Enter or update it below, then save to reload the DAG\./i),
    ).toBeInTheDocument();
    expect(screen.getByLabelText('API key')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /save key/i })).toBeInTheDocument();
    expect(screen.queryByText(/AgentHub is not reachable at http:\/\/agenthub\.local/i)).not.toBeInTheDocument();
  });

  test('shows the offline helper when AgentHub is unreachable', async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/health')) {
        return Promise.resolve({ ok: false, status: 503, statusText: 'Service Unavailable' });
      }
      throw new Error(`Unhandled fetch URL: ${url}`);
    });

    renderAgenthubPage();

    await waitFor(() => {
      expect(screen.getByText(/AgentHub is not reachable at http:\/\/agenthub\.local/i)).toBeInTheDocument();
    });
  });
});
