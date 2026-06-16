import { rerunTicketFromCurrentFrontier } from '../utils/api';

describe('rerunTicketFromCurrentFrontier', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify({ id: 'ticket-1' }),
    } as Response);
  });

  afterEach(() => {
    global.fetch = originalFetch;
    jest.clearAllMocks();
  });

  test('preserves existing callers by sending an empty JSON body without options', async () => {
    await rerunTicketFromCurrentFrontier('proj-1', 'ticket-1');

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/projects/proj-1/tickets/ticket-1/rerun-from-current-frontier'),
      expect.objectContaining({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      }),
    );
  });

  test('sends attempt_count when competing attempts are requested', async () => {
    await rerunTicketFromCurrentFrontier('proj-1', 'ticket-1', { attemptCount: 3 });

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/projects/proj-1/tickets/ticket-1/rerun-from-current-frontier'),
      expect.objectContaining({
        body: JSON.stringify({ attempt_count: 3 }),
      }),
    );
  });
});
