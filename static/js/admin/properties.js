/**
 * Admin Properties – client-side helpers
 */
(function (window) {
  'use strict';

  const Properties = {
    name: 'admin.properties',

    bulkAction(action) {
      const form = document.getElementById('bulk-properties-form');
      if (!form) return;
      const checked = form.querySelectorAll('input[name="prop_ids"]:checked');
      if (!checked.length) {
        alert('Select at least one property');
        return;
      }
      if (action === 'delete' && !confirm('Delete selected properties?')) return;
      form.querySelector('[name="action"]').value = action;
      form.submit();
    },

    async updateStatus(propId, status) {
      try {
        const formData = new FormData();
        formData.append('status', status);
        const res = await fetch(`/admin/properties/status/${propId}`, {
          method: 'POST',
          body: formData,
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return res.ok;
      } catch (e) {
        console.warn('[Properties] status update failed', e);
        return false;
      }
    },

    init() {
      console.log('[Admin] Properties module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Properties = Properties;
})(window);
