import React from 'react';
import { AppBar, Toolbar, Typography, Button, Box } from '@mui/material';
import { Link, useLocation } from 'react-router-dom';

interface NavbarProps {}

const Navbar: React.FC<NavbarProps> = () => {
  const location = useLocation();

  return (
    <AppBar position="static" elevation={0}>
      <Toolbar>
        <Typography
          variant="h6"
          component={Link}
          to="/"
          sx={{
            flex: 1,
            color: 'text.primary',
            textDecoration: 'none',
            fontWeight: 700,
            letterSpacing: '-0.02em',
          }}
        >
          Terarchitect
        </Typography>
        <Box sx={{ display: 'flex', gap: 2 }}>
          <Button
            component={Link}
            to="/projects"
            color={
              location.pathname === '/projects' || location.pathname === '/'
                ? 'primary'
                : 'inherit'
            }
          >
            Projects
          </Button>
          <Button
            component={Link}
            to="/settings"
            color={location.pathname === '/settings' ? 'primary' : 'inherit'}
          >
            Settings
          </Button>
        </Box>
      </Toolbar>
    </AppBar>
  );
};

export default Navbar;
