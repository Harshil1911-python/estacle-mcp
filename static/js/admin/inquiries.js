/**
 * Admin Inquiries – client-side helpers
 */
(function (window) {
  'use strict';

  const Inquiries = {
    name: 'admin.inquiries',

    async updateStatus(inqId, status) {
      try {
        const formData = new FormData();
        formData.append('status', status);
        const res = await fetch(`/admin/inquiries/status/${inqId}`, {
          method: 'POST',
          body: formData,
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return res.ok;
      } catch (e) {
        console.warn('[Inquiries] status update failed', e);
        return false;
      }
    },

    init() {
      console.log('[Admin] Inquiries module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Inquiries = Inquiries;
})(window);
