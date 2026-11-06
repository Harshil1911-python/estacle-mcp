/**
 * Admin Users – client-side helpers + offline-capable patterns
 */
(function (window) {
  'use strict';

  const Users = {
    name: 'admin.users',

    /** Bulk action helper */
    bulkAction(action) {
      const form = document.getElementById('bulk-users-form');
      if (!form) return;
      const checked = form.querySelectorAll('input[name="user_ids"]:checked');
      if (!checked.length) {
        alert('Select at least one user');
        return;
      }
      if (action === 'delete' && !confirm('Delete selected users?')) return;
      form.querySelector('[name="action"]').value = action;
      form.submit();
    },

    /** Toggle active status via fetch (works offline later with queue) */
    async toggleActive(userId) {
      try {
        const res = await fetch(`/admin/users/toggle/${userId}`, {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return res.ok;
      } catch (e) {
        console.warn('[Users] toggle failed (maybe offline)', e);
        return false;
      }
    },

    init() {
      console.log('[Admin] Users module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Users = Users;
})(window);
