/**
 * Admin Clients – client-side helpers
 */
(function (window) {
  'use strict';

  const Clients = {
    name: 'admin.clients',

    bulkAction(action) {
      const form = document.getElementById('bulk-clients-form');
      if (!form) return;
      const checked = form.querySelectorAll('input[name="client_ids"]:checked');
      if (!checked.length) {
        alert('Select at least one client');
        return;
      }
      form.querySelector('[name="action"]').value = action;
      form.submit();
    },

    init() {
      console.log('[Admin] Clients module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Clients = Clients;
})(window);
