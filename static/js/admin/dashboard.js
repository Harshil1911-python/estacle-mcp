/**
 * Admin Dashboard – client-side helpers
 * Designed so logic can later run offline (IndexedDB / local cache)
 */
(function (window) {
  'use strict';

  const Dashboard = {
    name: 'admin.dashboard',

    /** Refresh stats cards without full page reload (AJAX-ready) */
    async refreshStats() {
      try {
        const res = await fetch('/admin', { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        if (!res.ok) throw new Error('Failed to load dashboard');
        // In future: parse and update DOM or cache offline
        return true;
      } catch (e) {
        console.warn('[Dashboard] offline or error', e);
        return false;
      }
    },

    init() {
      console.log('[Admin] Dashboard module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Dashboard = Dashboard;
})(window);
