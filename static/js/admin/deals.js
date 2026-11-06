/**
 * Admin Deals – client-side helpers
 */
(function (window) {
  'use strict';

  const Deals = {
    name: 'admin.deals',

    async updateStage(dealId, stage) {
      try {
        const formData = new FormData();
        formData.append('stage', stage);
        const res = await fetch(`/admin/deals/stage/${dealId}`, {
          method: 'POST',
          body: formData,
          headers: { 'X-Requested-With': 'XMLHttpRequest' }
        });
        return res.ok;
      } catch (e) {
        console.warn('[Deals] stage update failed', e);
        return false;
      }
    },

    init() {
      console.log('[Admin] Deals module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Deals = Deals;
})(window);
