/**
 * Admin Tasks – client-side helpers
 */
(function (window) {
  'use strict';

  const Tasks = {
    name: 'admin.tasks',

    async updateStatus(taskId, status) {
      try {
        const formData = new FormData();
        formData.append('status', status);
        const res = await fetch(`/admin/tasks/status/${taskId}`, {
          method: 'POST',
          body: formData,
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return res.ok;
      } catch (e) {
        console.warn('[Tasks] status update failed', e);
        return false;
      }
    },

    init() {
      console.log('[Admin] Tasks module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Tasks = Tasks;
})(window);
