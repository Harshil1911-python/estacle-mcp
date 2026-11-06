/**
 * Admin Leads – client-side helpers
 */
(function (window) {
  'use strict';

  const Leads = {
    name: 'admin.leads',

    async updateStatus(leadId, status) {
      try {
        const formData = new FormData();
        formData.append('status', status);
        const res = await fetch(`/admin/leads/status/${leadId}`, {
          method: 'POST',
          body: formData,
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return res.ok;
      } catch (e) {
        console.warn('[Leads] status update failed', e);
        return false;
      }
    },

    init() {
      console.log('[Admin] Leads module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Leads = Leads;
})(window);
