import React from 'react';
import { AppBar, Toolbar, Typography, Button, Box } from '@mui/material';
import { Link, useLocation } from 'react-router-dom';

interface NavbarProps {}

const Navbar: React.FC<NavbarProps> = () => {
  const location = useLocation();

  return (
    <AppBar position="static" sx={{ backgroundColor: '#1a1a2e' }}>
      <Toolbar>
        <Typography
          variant="h6"
          component={Link}
          to="/"
          sx={{
            flex: 1,
            color: '#e0e0e0',
            textDecoration: 'none',
            fontWeight: 'bold',
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
        </Box>
      </Toolbar>
    </AppBar>
  );
};

export default Navbar;
