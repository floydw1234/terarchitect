import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import { MemoryRouter } from 'react-router-dom';
import { ThemeContextProvider } from '../contexts/ThemeContext';

jest.mock('../utils/api', () => ({
  getProjects: jest.fn(),
  getProjectAgenthubGraph: jest.fn(),
}));

import * as api from '../utils/api';
import AgenthubPage from '../pages/AgenthubPage';

const projectOne = {
  id: 'proj-1',
  name: 'Alpha',
  git_mode: 'swarm',
  accepted_frontier_id: 'bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222',
  shipped_frontier: 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
  github_resolved_sha: 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
};

const projectTwo = {
  id: 'proj-2',
  name: 'Beta',
  git_mode: 'swarm',
  accepted_frontier_id: null,
  shipped_frontier: null,
  github_resolved_sha: null,
};

function renderAgenthubPage() {
  return render(
    <ThemeContextProvider>
      <MemoryRouter>
        <AgenthubPage />
      </MemoryRouter>
    </ThemeContextProvider>,
  );
}

function graphPayload(project: any, overrides?: Partial<any>) {
  return {
    project,
    status: {
      code: 'ok',
      online: true,
      auth_configured: true,
      auth_mode: 'backend_api_key',
      project_scoped: true,
      message: null,
      guidance: null,
      ...(overrides?.status ?? {}),
    },
    scope: {
      anchor_hashes: [
        project.accepted_frontier_id,
        project.shipped_frontier,
        project.github_resolved_sha,
      ].filter(Boolean),
      frontier_hashes: [project.accepted_frontier_id, project.shipped_frontier].filter(Boolean),
      root_hashes: [project.github_resolved_sha].filter(Boolean),
      attempt_hashes: ['cccc3333cccc3333cccc3333cccc3333cccc3333'],
      channel_names: ['ticket-123456789012345678901234'],
      ...(overrides?.scope ?? {}),
    },
    graph: {
      commits: [
        {
          hash: 'cccc3333cccc3333cccc3333cccc3333cccc3333',
          parent_hash: 'bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222',
          agent_id: 'agent-beta',
          message: 'Finalize orchestrator pass',
          created_at: '2026-06-17T10:05:00.000Z',
        },
        {
          hash: 'bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222',
          parent_hash: 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
          agent_id: 'agent-alpha',
          message: 'Accepted frontier update',
          created_at: '2026-06-17T10:00:00.000Z',
        },
        {
          hash: 'aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111',
          parent_hash: '',
          agent_id: 'seed',
          message: 'Seed architecture graph',
          created_at: '2026-06-17T09:55:00.000Z',
        },
      ],
      nodes: [],
      leaves: [
        {
          hash: 'cccc3333cccc3333cccc3333cccc3333cccc3333',
          parent_hash: 'bbbb2222bbbb2222bbbb2222bbbb2222bbbb2222',
          agent_id: 'agent-beta',
          message: 'Finalize orchestrator pass',
          created_at: '2026-06-17T10:05:00.000Z',
        },
      ],
      channels: [
        { id: 1, name: 'ticket-123456789012345678901234', description: 'Ticket ledger', created_at: '2026-06-17T09:30:00.000Z' },
      ],
      posts: [
        {
          id: 99,
          channel_id: 1,
          channel_name: 'ticket-123456789012345678901234',
          agent_id: 'agent-beta',
          parent_id: null,
          content: 'Graph refresh deployed.',
          created_at: '2026-06-17T10:06:00.000Z',
        },
      ],
      root_hashes: ['aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111'],
      ...(overrides?.graph ?? {}),
    },
  };
}

describe('AgenthubPage', () => {
  beforeEach(() => {
    jest.resetAllMocks();
  });

  test('renders the backend project-scoped DAG without any browser API key prompt', async () => {
    (api.getProjects as jest.Mock).mockResolvedValue([projectOne]);
    (api.getProjectAgenthubGraph as jest.Mock).mockResolvedValue(graphPayload(projectOne));

    renderAgenthubPage();

    expect(await screen.findByText('AgentHub DAG for Alpha')).toBeInTheDocument();
    expect(screen.getAllByText('Finalize orchestrator pass').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Seed architecture graph').length).toBeGreaterThan(0);
    expect(screen.getByText('Project Frontier')).toBeInTheDocument();
    expect(screen.getByTestId('commit-dag-node-cccc3333cc')).toBeInTheDocument();
    expect(screen.getAllByText('#ticket-123456789012345678901234').length).toBeGreaterThan(0);
    expect(screen.getByText('Graph refresh deployed.')).toBeInTheDocument();
    expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
    expect(screen.queryByText(/saved key/i)).not.toBeInTheDocument();
    expect(screen.getByText(/browser never stores or sends an AgentHub API key/i)).toBeInTheDocument();
  });

  test('shows backend auth guidance instead of asking the browser for a key', async () => {
    (api.getProjects as jest.Mock).mockResolvedValue([projectOne]);
    (api.getProjectAgenthubGraph as jest.Mock).mockResolvedValue(
      graphPayload(projectOne, {
        status: {
          code: 'agenthub_auth_required',
          online: true,
          auth_configured: false,
          auth_mode: 'unauthenticated',
          message: 'Backend could not read AgentHub.',
          guidance: 'Set AGENTHUB_API_KEY in backend .env or enable dev auth bypass with AGENTHUB_AUTH_DISABLED=1 on AgentHub.',
        },
      }),
    );

    renderAgenthubPage();

    expect(await screen.findByText(/Backend could not read AgentHub\./i)).toBeInTheDocument();
    expect(screen.getByText(/Set AGENTHUB_API_KEY in backend \.env or enable dev auth bypass/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
  });

  test('switches projects and reloads the scoped graph through the backend api client', async () => {
    (api.getProjects as jest.Mock).mockResolvedValue([projectOne, projectTwo]);
    (api.getProjectAgenthubGraph as jest.Mock)
      .mockResolvedValueOnce(graphPayload(projectOne))
      .mockResolvedValueOnce(
        graphPayload(projectTwo, {
          graph: {
            commits: [],
            nodes: [],
            leaves: [],
            channels: [],
            posts: [],
            root_hashes: [],
          },
          scope: {
            anchor_hashes: [],
            frontier_hashes: [],
            root_hashes: [],
            attempt_hashes: [],
            channel_names: [],
          },
          status: {
            code: 'no_project_hashes',
            online: true,
            auth_configured: true,
            auth_mode: 'backend_api_key',
            message: 'This project has no accepted frontier, shipped frontier, source SHA, or recorded attempt hashes yet.',
            guidance: null,
          },
        }),
      );

    renderAgenthubPage();

    expect(await screen.findByText('AgentHub DAG for Alpha')).toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole('combobox', { name: /project/i }));
    fireEvent.click(await screen.findByText('Beta'));

    await waitFor(() => {
      expect(api.getProjectAgenthubGraph).toHaveBeenLastCalledWith('proj-2');
    });
    expect(await screen.findByText(/This project has no accepted frontier/i)).toBeInTheDocument();
  });
});
