/**
 * Admin JS entry point – loads feature modules
 * Structure prepared for future offline / IndexedDB support
 */
(function (window) {
  'use strict';

  window.EstacleAdmin = window.EstacleAdmin || {};

  function initAll() {
    const modules = [
      'Dashboard', 'Users', 'Properties', 'Clients',
      'Deals', 'Tasks', 'Leads', 'Inquiries', 'Search'
    ];
    modules.forEach(function (name) {
      const mod = window.EstacleAdmin[name];
      if (mod && typeof mod.init === 'function') {
        try { mod.init(); } catch (e) { console.warn(e); }
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initAll);
  } else {
    initAll();
  }
})(window);
