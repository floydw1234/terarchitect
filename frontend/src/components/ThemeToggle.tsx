import React from 'react';
import { Box, Switch, Tooltip, Typography } from '@mui/material';
import LightModeIcon from '@mui/icons-material/LightMode';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import { useThemeMode } from '../contexts/ThemeContext';

const ThemeToggle: React.FC = () => {
  const { mode, toggleTheme } = useThemeMode();
  const isDark = mode === 'dark';

  return (
    <Tooltip title={isDark ? 'Switch to light mode' : 'Switch to dark mode'} arrow>
      <Box
        sx={{
          display: 'flex',
          alignItems: 'center',
          gap: 0.75,
          px: 1,
          py: 0.5,
          borderRadius: 1,
          border: '1px solid',
          borderColor: isDark ? 'divider' : 'secondary.main',
          bgcolor: isDark ? 'background.paper' : 'info.light',
        }}
      >
        <LightModeIcon
          sx={{
            fontSize: 18,
            color: isDark ? 'text.secondary' : 'warning.main',
            transition: 'color 0.2s ease',
          }}
          aria-hidden="true"
        />
        <Switch
          checked={isDark}
          onChange={toggleTheme}
          size="small"
          inputProps={{
            'aria-label': isDark ? 'Switch to light mode' : 'Switch to dark mode',
            role: 'switch',
          }}
          sx={{
            '& .MuiSwitch-switchBase': {
              '&.Mui-checked': {
                color: 'primary.main',
                '& + .MuiSwitch-track': {
                  backgroundColor: 'primary.dark',
                  opacity: 0.7,
                },
              },
            },
            '& .MuiSwitch-track': {
              backgroundColor: isDark ? 'grey.600' : 'secondary.light',
              opacity: 0.6,
            },
            '& .MuiSwitch-thumb': {
              backgroundColor: isDark ? 'primary.main' : 'warning.main',
            },
          }}
        />
        <DarkModeIcon
          sx={{
            fontSize: 18,
            color: isDark ? 'primary.main' : 'text.secondary',
            transition: 'color 0.2s ease',
          }}
          aria-hidden="true"
        />
        <Typography
          variant="caption"
          sx={{
            fontWeight: 600,
            color: 'text.secondary',
            display: { xs: 'none', sm: 'block' },
            minWidth: 36,
          }}
        >
          {isDark ? 'Dark' : 'Light'}
        </Typography>
      </Box>
    </Tooltip>
  );
};

export default ThemeToggle;
