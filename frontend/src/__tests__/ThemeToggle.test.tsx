import React from 'react';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom';

import { ThemeContextProvider, useThemeMode, THEME_STORAGE_KEY, type ThemeMode } from '../contexts/ThemeContext';
import ThemeToggle from '../components/ThemeToggle';

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => { store[key] = value; },
    removeItem: (key: string) => { delete store[key]; },
    clear: () => { store = {}; },
  };
})();

Object.defineProperty(window, 'localStorage', { value: localStorageMock });

function TestComponent() {
  const { mode } = useThemeMode();
  return (
    <div>
      <span data-testid="current-mode">{mode}</span>
      <ThemeToggle />
    </div>
  );
}

function renderWithThemeProvider() {
  return render(
    <ThemeContextProvider>
      <TestComponent />
    </ThemeContextProvider>
  );
}

beforeEach(() => {
  localStorageMock.clear();
  document.documentElement.removeAttribute('data-theme');
});

describe('ThemeToggle', () => {
  test('renders with light mode by default', () => {
    renderWithThemeProvider();

    expect(screen.getByTestId('current-mode')).toHaveTextContent('light');
    expect(screen.getByText('Light')).toBeInTheDocument();
    expect(screen.getByRole('switch')).not.toBeChecked();
  });

  test('toggles to dark mode when clicked', async () => {
    const user = userEvent.setup();
    renderWithThemeProvider();

    expect(screen.getByTestId('current-mode')).toHaveTextContent('light');

    await user.click(screen.getByRole('switch'));

    expect(screen.getByTestId('current-mode')).toHaveTextContent('dark');
    expect(screen.getByText('Dark')).toBeInTheDocument();
    expect(screen.getByRole('switch')).toBeChecked();
  });

  test('toggles back to light mode when clicked twice', async () => {
    const user = userEvent.setup();
    renderWithThemeProvider();

    await user.click(screen.getByRole('switch'));
    expect(screen.getByTestId('current-mode')).toHaveTextContent('dark');

    await user.click(screen.getByRole('switch'));
    expect(screen.getByTestId('current-mode')).toHaveTextContent('light');
    expect(screen.getByText('Light')).toBeInTheDocument();
  });

  test('persists theme preference to localStorage', async () => {
    const user = userEvent.setup();
    renderWithThemeProvider();

    expect(localStorageMock.getItem(THEME_STORAGE_KEY)).toBe('light');

    await user.click(screen.getByRole('switch'));

    expect(localStorageMock.getItem(THEME_STORAGE_KEY)).toBe('dark');
  });

  test('initializes from localStorage preference', () => {
    localStorageMock.setItem(THEME_STORAGE_KEY, 'dark');

    renderWithThemeProvider();

    expect(screen.getByTestId('current-mode')).toHaveTextContent('dark');
    expect(screen.getByRole('switch')).toBeChecked();
  });

  test('sets data-theme attribute on document element', async () => {
    const user = userEvent.setup();
    renderWithThemeProvider();

    expect(document.documentElement.getAttribute('data-theme')).toBe('light');

    await user.click(screen.getByRole('switch'));

    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
  });

  test('has accessible label for screen readers', () => {
    renderWithThemeProvider();

    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-label', 'Switch to dark mode');
  });

  test('updates accessible label after toggle', async () => {
    const user = userEvent.setup();
    renderWithThemeProvider();

    const toggle = screen.getByRole('switch');
    expect(toggle).toHaveAttribute('aria-label', 'Switch to dark mode');

    await user.click(toggle);

    expect(toggle).toHaveAttribute('aria-label', 'Switch to light mode');
  });

  test('is keyboard accessible', async () => {
    const user = userEvent.setup();
    renderWithThemeProvider();

    const toggle = screen.getByRole('switch');
    toggle.focus();
    expect(toggle).toHaveFocus();

    await user.keyboard(' ');

    expect(screen.getByTestId('current-mode')).toHaveTextContent('dark');
  });

  test('defaults to light mode for invalid localStorage value', () => {
    localStorageMock.setItem(THEME_STORAGE_KEY, 'invalid');

    renderWithThemeProvider();

    expect(screen.getByTestId('current-mode')).toHaveTextContent('light');
  });
});
