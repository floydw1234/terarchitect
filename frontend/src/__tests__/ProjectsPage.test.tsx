import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider, createTheme } from '@mui/material';

jest.mock('../utils/api', () => ({
  getProjects: jest.fn(),
  createProject: jest.fn(),
  deleteProject: jest.fn(),
  getExecutionReady: jest.fn(),
}));

import * as api from '../utils/api';
import ProjectsPage from '../pages/ProjectsPage';

const theme = createTheme({ palette: { mode: 'dark' } });

function renderProjectsPage() {
  return render(
    <ThemeProvider theme={theme}>
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  (api.getProjects as jest.Mock).mockResolvedValue([]);
  (api.getExecutionReady as jest.Mock).mockResolvedValue({ features: { composite_workspace: false } });
});

test('creates a project with GitHub-first defaults', async () => {
  const user = userEvent.setup();
  (api.createProject as jest.Mock).mockResolvedValue({
    id: 'proj-1',
    name: 'Wizard',
    source_type: 'github',
    github_url: 'https://github.com/acme/wizard',
    github_ref: 'main',
    execution_mode: 'docker',
    git_mode: 'swarm',
  });

  renderProjectsPage();

  await screen.findByText('No projects yet. Create one to get started!');

  await user.click(screen.getByRole('button', { name: 'Create' }));
  expect(screen.getByRole('button', { name: /^Create$/ })).toBeDisabled();

  await user.type(screen.getByPlaceholderText('Enter project name'), 'Wizard');
  await user.type(screen.getByPlaceholderText('https://github.com/owner/repo'), 'https://github.com/acme/wizard');
  await user.click(screen.getByRole('button', { name: /^Create$/ }));

  await waitFor(() => {
    expect(api.createProject).toHaveBeenCalledWith({
      name: 'Wizard',
      description: undefined,
      source_type: 'github',
      github_url: 'https://github.com/acme/wizard',
      base_ref: 'main',
      github_ref: 'main',
      import_to_agenthub: true,
      execution_mode: 'docker',
      git_mode: 'swarm',
      project_path: undefined,
      is_existing_repo: false,
    });
  });
});

test('keeps legacy local-path creation usable', async () => {
  const user = userEvent.setup();
  (api.createProject as jest.Mock).mockResolvedValue({
    id: 'proj-2',
    name: 'Local Wizard',
    source_type: 'local_path',
    project_path: '/repo/local-wizard',
    execution_mode: 'local',
    git_mode: 'swarm',
  });

  renderProjectsPage();

  await screen.findByText('No projects yet. Create one to get started!');
  await user.click(screen.getByRole('button', { name: 'Create' }));
  await user.type(screen.getByPlaceholderText('Enter project name'), 'Local Wizard');

  fireEvent.mouseDown(screen.getAllByRole('combobox')[1]);
  const listbox = await screen.findByRole('listbox');
  fireEvent.click(within(listbox).getByText('Local path (advanced/dev)'));

  const projectPathInput = await screen.findByPlaceholderText('/path/to/project/on/host');
  await user.type(projectPathInput, '/repo/local-wizard');
  await user.click(screen.getByRole('button', { name: /^Create$/ }));

  await waitFor(() => {
    expect(api.createProject).toHaveBeenCalledWith({
      name: 'Local Wizard',
      description: undefined,
      source_type: 'local_path',
      github_url: undefined,
      base_ref: undefined,
      github_ref: undefined,
      import_to_agenthub: undefined,
      execution_mode: 'local',
      git_mode: 'swarm',
      project_path: '/repo/local-wizard',
      is_existing_repo: false,
    });
  });
});
