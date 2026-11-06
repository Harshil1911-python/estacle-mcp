/**
 * Admin Global Search – client-side helpers
 */
(function (window) {
  'use strict';

  const Search = {
    name: 'admin.search',

    init() {
      const input = document.getElementById('global-search-input');
      if (input) {
        input.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') {
            e.preventDefault();
            this.form && this.form.submit();
          }
        });
      }
      console.log('[Admin] Search module loaded');
    }
  };

  window.EstacleAdmin = window.EstacleAdmin || {};
  window.EstacleAdmin.Search = Search;
})(window);
