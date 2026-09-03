import React, { createContext, useContext, useState, useEffect, useMemo, useCallback, type ReactNode } from 'react';
import { createTheme, type Theme } from '@mui/material/styles';

const THEME_STORAGE_KEY = 'terarchitect-theme-mode';

type ThemeMode = 'light' | 'dark';

interface ThemeContextValue {
  mode: ThemeMode;
  toggleTheme: () => void;
  theme: Theme;
}

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

const lightTheme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: '#0085FA',
      light: '#45C3F8',
      dark: '#0066cc',
    },
    secondary: {
      main: '#45C3F8',
      light: '#7dd4fa',
      dark: '#0085FA',
    },
    info: {
      main: '#0085FA',
      light: '#E9F7FF',
    },
    success: {
      main: '#059669',
      light: '#d1fae5',
    },
    warning: {
      main: '#d97706',
      light: '#fef3c7',
    },
    error: {
      main: '#dc2626',
      light: '#fee2e2',
    },
    background: {
      default: '#E9F7FF',
      paper: '#FFFFFF',
    },
    text: {
      primary: 'rgba(0, 0, 0, 0.87)',
      secondary: 'rgba(0, 0, 0, 0.6)',
    },
    divider: '#D4D4D4',
  },
  typography: {
    fontFamily: '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h4: {
      fontSize: '2rem',
      fontWeight: 700,
      letterSpacing: '-0.04em',
    },
    h5: {
      fontSize: '1.5rem',
      fontWeight: 700,
      letterSpacing: '-0.035em',
    },
    h6: {
      fontWeight: 700,
      letterSpacing: '-0.025em',
    },
    subtitle1: {
      fontWeight: 600,
      letterSpacing: '-0.02em',
    },
    subtitle2: {
      fontWeight: 600,
      letterSpacing: '-0.01em',
    },
    button: {
      fontWeight: 600,
      letterSpacing: '-0.01em',
    },
    overline: {
      letterSpacing: '0.16em',
      fontWeight: 700,
    },
  },
  shape: {
    borderRadius: 4,
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: '#E9F7FF',
          color: 'rgba(0, 0, 0, 0.87)',
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          background: '#FFFFFF',
          borderRadius: 6,
          border: '1px solid #D4D4D4',
          boxShadow: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          background: '#FFFFFF',
          borderRadius: 6,
          border: '1px solid #D4D4D4',
          boxShadow: 'none',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          borderRadius: 4,
          paddingInline: 20,
          minHeight: 40,
          fontWeight: 600,
          '&:focus-visible': {
            outline: '2px solid #0085FA',
            outlineOffset: 2,
          },
        },
        contained: {
          boxShadow: 'none',
          '&:hover': {
            boxShadow: 'none',
          },
        },
        containedPrimary: {
          backgroundColor: '#0085FA',
          '&:hover': {
            backgroundColor: '#0066cc',
          },
        },
        outlined: {
          borderColor: '#D4D4D4',
          '&:hover': {
            borderColor: '#0085FA',
            backgroundColor: 'rgba(0, 133, 250, 0.04)',
          },
        },
        text: {
          '&:hover': {
            backgroundColor: 'rgba(0, 133, 250, 0.08)',
          },
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#FFFFFF',
          borderBottom: '2px solid #E9F7FF',
          boxShadow: 'none',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 4,
        },
        colorPrimary: {
          backgroundColor: '#0085FA',
          color: '#FFFFFF',
        },
        colorSecondary: {
          backgroundColor: '#45C3F8',
          color: '#FFFFFF',
        },
        colorInfo: {
          backgroundColor: '#E9F7FF',
          color: '#0085FA',
          border: '1px solid #45C3F8',
        },
        colorSuccess: {
          backgroundColor: '#d1fae5',
          color: '#059669',
        },
        colorWarning: {
          backgroundColor: '#fef3c7',
          color: '#d97706',
        },
        colorError: {
          backgroundColor: '#fee2e2',
          color: '#dc2626',
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          background: '#FFFFFF',
          border: '1px solid #D4D4D4',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.15)',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            backgroundColor: '#FFFFFF',
            '& fieldset': { borderColor: '#D4D4D4' },
            '&:hover fieldset': { borderColor: '#0085FA' },
            '&.Mui-focused fieldset': { borderColor: '#0085FA', borderWidth: 2 },
          },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 4,
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderColor: '#0085FA',
            borderWidth: 2,
          },
        },
      },
    },
    MuiCheckbox: {
      styleOverrides: {
        root: {
          color: '#D4D4D4',
          '&.Mui-checked': {
            color: '#0085FA',
          },
        },
      },
    },
    MuiSelect: {
      styleOverrides: {
        root: {
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderColor: '#0085FA',
            borderWidth: 2,
          },
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        standardSuccess: {
          backgroundColor: '#d1fae5',
          color: '#065f46',
        },
        standardWarning: {
          backgroundColor: '#fef3c7',
          color: '#92400e',
        },
        standardError: {
          backgroundColor: '#fee2e2',
          color: '#991b1b',
        },
        standardInfo: {
          backgroundColor: '#E9F7FF',
          color: '#0066cc',
        },
      },
    },
  },
});

const darkTheme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: '#45C3F8',
      light: '#7dd4fa',
      dark: '#0085FA',
    },
    secondary: {
      main: '#0085FA',
      light: '#45C3F8',
      dark: '#0066cc',
    },
    info: {
      main: '#45C3F8',
      light: '#1a3a52',
    },
    success: {
      main: '#34d399',
      light: '#064e3b',
    },
    warning: {
      main: '#fbbf24',
      light: '#78350f',
    },
    error: {
      main: '#f87171',
      light: '#7f1d1d',
    },
    background: {
      default: '#0d1b2a',
      paper: '#1b2838',
    },
    text: {
      primary: 'rgba(255, 255, 255, 0.92)',
      secondary: 'rgba(255, 255, 255, 0.64)',
    },
    divider: '#2d4356',
  },
  typography: {
    fontFamily: '"Inter", "SF Pro Display", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    h4: {
      fontSize: '2rem',
      fontWeight: 700,
      letterSpacing: '-0.04em',
    },
    h5: {
      fontSize: '1.5rem',
      fontWeight: 700,
      letterSpacing: '-0.035em',
    },
    h6: {
      fontWeight: 700,
      letterSpacing: '-0.025em',
    },
    subtitle1: {
      fontWeight: 600,
      letterSpacing: '-0.02em',
    },
    subtitle2: {
      fontWeight: 600,
      letterSpacing: '-0.01em',
    },
    button: {
      fontWeight: 600,
      letterSpacing: '-0.01em',
    },
    overline: {
      letterSpacing: '0.16em',
      fontWeight: 700,
    },
  },
  shape: {
    borderRadius: 4,
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: '#0d1b2a',
          color: 'rgba(255, 255, 255, 0.92)',
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          background: '#1b2838',
          borderRadius: 6,
          border: '1px solid #2d4356',
          boxShadow: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          background: '#1b2838',
          borderRadius: 6,
          border: '1px solid #2d4356',
          boxShadow: 'none',
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          borderRadius: 4,
          paddingInline: 20,
          minHeight: 40,
          fontWeight: 600,
          '&:focus-visible': {
            outline: '2px solid #45C3F8',
            outlineOffset: 2,
          },
        },
        contained: {
          boxShadow: 'none',
          '&:hover': {
            boxShadow: 'none',
          },
        },
        containedPrimary: {
          backgroundColor: '#45C3F8',
          color: '#0d1b2a',
          '&:hover': {
            backgroundColor: '#7dd4fa',
          },
        },
        outlined: {
          borderColor: '#2d4356',
          color: 'rgba(255, 255, 255, 0.92)',
          '&:hover': {
            borderColor: '#45C3F8',
            backgroundColor: 'rgba(69, 195, 248, 0.08)',
          },
        },
        text: {
          color: 'rgba(255, 255, 255, 0.92)',
          '&:hover': {
            backgroundColor: 'rgba(69, 195, 248, 0.12)',
          },
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: '#1b2838',
          borderBottom: '2px solid #45C3F8',
          boxShadow: 'none',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          borderRadius: 4,
        },
        colorPrimary: {
          backgroundColor: '#45C3F8',
          color: '#0d1b2a',
        },
        colorSecondary: {
          backgroundColor: '#0085FA',
          color: '#FFFFFF',
        },
        colorInfo: {
          backgroundColor: '#1a3a52',
          color: '#45C3F8',
          border: '1px solid #2d4356',
        },
        colorSuccess: {
          backgroundColor: '#064e3b',
          color: '#34d399',
        },
        colorWarning: {
          backgroundColor: '#78350f',
          color: '#fbbf24',
        },
        colorError: {
          backgroundColor: '#7f1d1d',
          color: '#f87171',
        },
      },
    },
    MuiDialog: {
      styleOverrides: {
        paper: {
          background: '#1b2838',
          border: '1px solid #2d4356',
          boxShadow: '0 4px 20px rgba(0, 0, 0, 0.5)',
        },
      },
    },
    MuiTextField: {
      styleOverrides: {
        root: {
          '& .MuiOutlinedInput-root': {
            backgroundColor: '#0d1b2a',
            '& fieldset': { borderColor: '#2d4356' },
            '&:hover fieldset': { borderColor: '#45C3F8' },
            '&.Mui-focused fieldset': { borderColor: '#45C3F8', borderWidth: 2 },
          },
          '& .MuiInputBase-input': {
            color: 'rgba(255, 255, 255, 0.92)',
          },
          '& .MuiInputLabel-root': {
            color: 'rgba(255, 255, 255, 0.64)',
          },
        },
      },
    },
    MuiOutlinedInput: {
      styleOverrides: {
        root: {
          borderRadius: 4,
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderColor: '#45C3F8',
            borderWidth: 2,
          },
        },
        notchedOutline: {
          borderColor: '#2d4356',
        },
      },
    },
    MuiCheckbox: {
      styleOverrides: {
        root: {
          color: '#2d4356',
          '&.Mui-checked': {
            color: '#45C3F8',
          },
        },
      },
    },
    MuiSelect: {
      styleOverrides: {
        root: {
          '&.Mui-focused .MuiOutlinedInput-notchedOutline': {
            borderColor: '#45C3F8',
            borderWidth: 2,
          },
        },
        icon: {
          color: 'rgba(255, 255, 255, 0.64)',
        },
      },
    },
    MuiAlert: {
      styleOverrides: {
        standardSuccess: {
          backgroundColor: '#064e3b',
          color: '#34d399',
        },
        standardWarning: {
          backgroundColor: '#78350f',
          color: '#fbbf24',
        },
        standardError: {
          backgroundColor: '#7f1d1d',
          color: '#f87171',
        },
        standardInfo: {
          backgroundColor: '#1a3a52',
          color: '#45C3F8',
        },
      },
    },
    MuiMenu: {
      styleOverrides: {
        paper: {
          backgroundColor: '#1b2838',
          border: '1px solid #2d4356',
        },
      },
    },
    MuiMenuItem: {
      styleOverrides: {
        root: {
          '&:hover': {
            backgroundColor: 'rgba(69, 195, 248, 0.08)',
          },
          '&.Mui-selected': {
            backgroundColor: 'rgba(69, 195, 248, 0.16)',
            '&:hover': {
              backgroundColor: 'rgba(69, 195, 248, 0.24)',
            },
          },
        },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: '#1b2838',
          color: 'rgba(255, 255, 255, 0.92)',
          border: '1px solid #2d4356',
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.64)',
          '&:hover': {
            backgroundColor: 'rgba(69, 195, 248, 0.12)',
          },
        },
      },
    },
    MuiInputLabel: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.64)',
          '&.Mui-focused': {
            color: '#45C3F8',
          },
        },
      },
    },
    MuiFormHelperText: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.5)',
        },
      },
    },
    MuiDialogTitle: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.92)',
        },
      },
    },
    MuiDialogContent: {
      styleOverrides: {
        root: {
          color: 'rgba(255, 255, 255, 0.92)',
        },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: {
          borderColor: '#2d4356',
        },
      },
    },
    MuiDivider: {
      styleOverrides: {
        root: {
          borderColor: '#2d4356',
        },
      },
    },
    MuiCircularProgress: {
      styleOverrides: {
        colorPrimary: {
          color: '#45C3F8',
        },
      },
    },
    MuiSwitch: {
      styleOverrides: {
        switchBase: {
          color: '#2d4356',
          '&.Mui-checked': {
            color: '#45C3F8',
            '& + .MuiSwitch-track': {
              backgroundColor: '#0085FA',
            },
          },
        },
        track: {
          backgroundColor: '#2d4356',
        },
      },
    },
  },
});

function getInitialMode(): ThemeMode {
  if (typeof window === 'undefined') {
    return 'light';
  }
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'dark' || stored === 'light') {
      return stored;
    }
  } catch {
    // localStorage not available
  }
  return 'light';
}

interface ThemeProviderProps {
  children: ReactNode;
}

export function ThemeContextProvider({ children }: ThemeProviderProps) {
  const [mode, setMode] = useState<ThemeMode>(getInitialMode);

  useEffect(() => {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, mode);
    } catch {
      // localStorage not available
    }
    document.documentElement.setAttribute('data-theme', mode);
  }, [mode]);

  const toggleTheme = useCallback(() => {
    setMode((prev) => (prev === 'light' ? 'dark' : 'light'));
  }, []);

  const theme = useMemo(() => (mode === 'light' ? lightTheme : darkTheme), [mode]);

  const value = useMemo(
    () => ({
      mode,
      toggleTheme,
      theme,
    }),
    [mode, toggleTheme, theme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useThemeMode() {
  const context = useContext(ThemeContext);
  if (context === undefined) {
    throw new Error('useThemeMode must be used within a ThemeContextProvider');
  }
  return context;
}

export { THEME_STORAGE_KEY };
export type { ThemeMode };
