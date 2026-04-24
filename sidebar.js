(() => {
  // ─── Nav config ──────────────────────────────────────────────────────────────
  const NAV = [
    {
      id: 'uk-aq',
      label: 'UK-AQ',
      icon: '◆',
      defaultExpanded: true,
      children: [
        { label: 'Hex Map',     icon: '◆', href: '/uk-aq/hex-map' },
        { label: 'Sensors',     icon: '●', href: '/uk-aq/sensors' },
        { label: 'Sensors Map', icon: '◉', href: '/uk-aq/sensors-map' },
      ],
    },
    {
      id: 'data-explorer',
      label: 'Data Explorer',
      icon: '↗',
      defaultExpanded: false,
      children: [
        { label: 'Bubble Chart',       icon: '○', href: '/data-explorer/bubble' },
        { label: 'Line Chart',         icon: '↗', href: '/data-explorer/line' },
        { label: 'Ecodesign Replaces', icon: '≡', href: '/data-explorer/eco' },
        { label: 'Category Info',      icon: 'i', href: '/data-explorer/category-info' },
        { label: 'User Guide',         icon: '?', href: '/data-explorer/user-guide' },
      ],
    },
    {
      id: 'resources',
      label: 'Resources',
      icon: '⎆',
      defaultExpanded: false,
      children: [],
    },
    {
      id: 'contact',
      label: 'Contact',
      icon: '✉',
      defaultExpanded: false,
      children: [],
    },
  ];

  // ─── State ────────────────────────────────────────────────────────────────────
  const EXPANDED  = 'expanded';
  const COLLAPSED = 'collapsed';
  const MINI      = 'mini';
  const DRAWER    = 'drawer';

  const sectionExpanded = {};
  NAV.forEach(s => { sectionExpanded[s.id] = s.defaultExpanded; });

  let autoCollapseTimer = null;

  function getBreakpoint() {
    const w = window.innerWidth;
    if (w < 768)  return 'mobile';
    if (w < 1100) return 'tablet';
    return 'desktop';
  }

  function isHomePage() {
    const p = location.pathname;
    return p === '/' || p === '/index.html' || p === '';
  }

  function getState() {
    return document.body.getAttribute('data-sidebar-state');
  }

  function setState(state) {
    document.body.setAttribute('data-sidebar-state', state);
    const overlay = document.getElementById('cic-sidebar-overlay');
    if (overlay) overlay.classList.toggle('visible', state === DRAWER);
  }

  function scheduleAutoCollapse() {
    clearTimeout(autoCollapseTimer);
    autoCollapseTimer = setTimeout(() => {
      if (getBreakpoint() === 'desktop' && getState() === EXPANDED) {
        setState(COLLAPSED);
      }
    }, 3000);
  }

  // ─── CSS ──────────────────────────────────────────────────────────────────────
  const CSS = `
    :root {
      --cic-accent:          #3C78AC;
      --cic-accent-deep:     #285A84;
      --cic-ink:             #101822;
      --cic-ink-1:           #1b2a38;
      --cic-ink-2:           #3a4a5a;
      --cic-ink-3:           #6b7a88;
      --cic-ink-4:           #9aa7b3;
      --cic-line:            #e4e6ea;
      --cic-line-soft:       #eef0f3;
      --cic-surface:         #ffffff;
      --cic-surface-2:       #fbfaf6;
      --cic-bg:              #f6f5f1;
      --cic-radius:          10px;
      --cic-w:               232px;
      --cic-mini-w:          64px;
      --cic-drawer-w:        280px;
      --cic-transition:      0.3s ease;
    }

    /* ── Shell layout ── */
    body {
      transition: padding-left var(--cic-transition);
    }
    body[data-sidebar-state="expanded"]  { padding-left: var(--cic-w); }
    body[data-sidebar-state="collapsed"] { padding-left: 0; }
    body[data-sidebar-state="mini"]      { padding-left: var(--cic-mini-w); }
    body[data-sidebar-state="drawer"]    { padding-left: 0; }

    /* ── Sidebar panel ── */
    #cic-sidebar {
      position: fixed;
      top: 0; left: 0;
      height: 100vh;
      width: var(--cic-w);
      background: var(--cic-surface);
      border-right: 1px solid var(--cic-line);
      display: flex;
      flex-direction: column;
      z-index: 200;
      overflow-y: auto;
      overflow-x: hidden;
      transition: transform var(--cic-transition), width var(--cic-transition);
    }

    body[data-sidebar-state="collapsed"] #cic-sidebar {
      transform: translateX(calc(-1 * var(--cic-w)));
    }
    body[data-sidebar-state="mini"] #cic-sidebar {
      width: var(--cic-mini-w);
      transform: none;
    }
    body[data-sidebar-state="drawer"] #cic-sidebar {
      width: var(--cic-drawer-w);
      transform: translateX(calc(-1 * var(--cic-drawer-w)));
      transition: transform var(--cic-transition);
    }
    body[data-sidebar-state="drawer"].cic-drawer-open #cic-sidebar {
      transform: translateX(0);
    }

    /* ── Overlay (drawer backdrop) ── */
    #cic-sidebar-overlay {
      display: none;
      position: fixed;
      inset: 0;
      background: rgba(16,24,34,0.35);
      z-index: 199;
      opacity: 0;
      transition: opacity var(--cic-transition);
    }
    #cic-sidebar-overlay.visible {
      display: block;
    }
    body.cic-drawer-open #cic-sidebar-overlay {
      opacity: 1;
    }

    /* ── Hamburger button ── */
    #cic-hamburger {
      position: fixed;
      top: 10px; left: 10px;
      z-index: 300;
      background: none;
      border: none;
      cursor: pointer;
      padding: 4px;
      width: 52px; height: 52px;
      display: flex; align-items: center; justify-content: center;
      border-radius: 8px;
      transition: background 0.2s;
    }
    #cic-hamburger:hover { background: rgba(16,24,34,0.07); }
    #cic-hamburger img  { width: 40px; height: 40px; object-fit: contain; display: block; }

    /* Hide hamburger when sidebar is expanded on desktop */
    body[data-sidebar-state="expanded"]  #cic-hamburger { opacity: 0.45; }
    body[data-sidebar-state="collapsed"] #cic-hamburger { opacity: 1; }
    body[data-sidebar-state="mini"]      #cic-hamburger { display: none; }
    body[data-sidebar-state="drawer"]    #cic-hamburger { opacity: 1; }

    /* ── Brand ── */
    #cic-sidebar-brand {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 14px 14px 12px;
      border-bottom: 1px solid var(--cic-line-soft);
      text-decoration: none;
      color: var(--cic-accent-deep);
      flex-shrink: 0;
    }
    #cic-sidebar-brand img {
      width: 36px; height: 36px;
      border-radius: 6px;
      object-fit: contain;
      flex-shrink: 0;
    }
    #cic-sidebar-brand .cic-brand-name {
      font-family: -apple-system, 'Inter', system-ui, sans-serif;
      font-weight: 700;
      font-size: 14px;
      color: var(--cic-ink-1);
      letter-spacing: -0.01em;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
    }
    body[data-sidebar-state="mini"] #cic-sidebar-brand .cic-brand-name { display: none; }

    /* ── Nav sections ── */
    .cic-nav {
      flex: 1;
      padding: 10px 8px;
      display: flex;
      flex-direction: column;
      gap: 2px;
    }

    .cic-section-toggle {
      display: flex;
      align-items: center;
      gap: 8px;
      width: 100%;
      background: none;
      border: none;
      cursor: pointer;
      padding: 7px 8px;
      border-radius: 7px;
      color: var(--cic-ink-3);
      font-size: 10.5px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-weight: 600;
      font-family: inherit;
      text-align: left;
    }
    .cic-section-toggle:hover { background: var(--cic-surface-2); }
    .cic-section-toggle .cic-chevron {
      margin-left: auto;
      font-style: normal;
      font-size: 10px;
      transition: transform 0.2s;
      flex-shrink: 0;
    }
    .cic-section-toggle[aria-expanded="true"] .cic-chevron { transform: rotate(90deg); }
    body[data-sidebar-state="mini"] .cic-section-toggle .cic-chevron,
    body[data-sidebar-state="mini"] .cic-section-toggle .cic-section-label { display: none; }
    body[data-sidebar-state="mini"] .cic-section-toggle { justify-content: center; }

    .cic-section-children {
      display: flex;
      flex-direction: column;
      gap: 1px;
      overflow: hidden;
      max-height: 400px;
      transition: max-height 0.25s ease, opacity 0.2s;
      opacity: 1;
    }
    .cic-section-children[aria-hidden="true"] {
      max-height: 0;
      opacity: 0;
    }

    /* ── Nav items ── */
    .cic-nav-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 10px 8px 14px;
      border-radius: 7px;
      color: var(--cic-ink-2);
      font-size: 13.5px;
      font-weight: 500;
      font-family: -apple-system, 'Inter', system-ui, sans-serif;
      text-decoration: none;
      border: 1px solid transparent;
      white-space: nowrap;
      overflow: hidden;
    }
    .cic-nav-item:hover {
      background: var(--cic-surface-2);
      color: var(--cic-ink-1);
      text-decoration: none;
    }
    .cic-nav-item.active {
      background: color-mix(in oklab, var(--cic-accent) 10%, white);
      color: var(--cic-accent-deep);
      border-color: color-mix(in oklab, var(--cic-accent) 25%, white);
    }
    .cic-nav-icon {
      width: 18px; flex-shrink: 0;
      display: inline-flex; align-items: center; justify-content: center;
      font-style: normal; font-size: 12px;
    }
    .cic-nav-label { overflow: hidden; text-overflow: ellipsis; }
    body[data-sidebar-state="mini"] .cic-nav-label  { display: none; }
    body[data-sidebar-state="mini"] .cic-nav-item   { padding: 10px; justify-content: center; }

    /* ── Footer ── */
    #cic-sidebar-footer {
      padding: 10px 14px 14px;
      border-top: 1px solid var(--cic-line-soft);
      font-size: 11px;
      color: var(--cic-ink-4);
      white-space: nowrap;
      overflow: hidden;
    }
    body[data-sidebar-state="mini"] #cic-sidebar-footer { display: none; }
  `;

  // ─── HTML builders ────────────────────────────────────────────────────────────
  function buildNavItem(item) {
    const path = location.pathname;
    const isActive = item.href !== '#' && path.includes(item.href);
    return `
      <a class="cic-nav-item${isActive ? ' active' : ''}" href="${item.href}">
        <i class="cic-nav-icon">${item.icon}</i>
        <span class="cic-nav-label">${item.label}</span>
      </a>`;
  }

  function buildSection(section) {
    const expanded = sectionExpanded[section.id];
    const hasChildren = section.children.length > 0;
    const childrenHtml = hasChildren
      ? section.children.map(buildNavItem).join('')
      : '';

    return `
      <div class="cic-nav-section" id="cic-section-${section.id}">
        <button
          class="cic-section-toggle"
          aria-expanded="${expanded}"
          data-section="${section.id}"
          ${!hasChildren ? 'disabled style="cursor:default;opacity:0.6;"' : ''}
        >
          <i class="cic-nav-icon">${section.icon}</i>
          <span class="cic-section-label">${section.label}</span>
          ${hasChildren ? '<i class="cic-chevron">▶</i>' : ''}
        </button>
        ${hasChildren ? `
        <div class="cic-section-children" aria-hidden="${!expanded}">
          ${childrenHtml}
        </div>` : ''}
      </div>`;
  }

  function buildSidebar() {
    const imgBase = location.origin;
    return `
      <a id="cic-sidebar-brand" href="/">
        <img src="${imgBase}/images/CIC - Square - Border - Words - Alpha 360x360.png" alt="CIC">
        <span class="cic-brand-name">Chronic Illness<br>Channel</span>
      </a>
      <nav class="cic-nav" aria-label="Site navigation">
        ${NAV.map(buildSection).join('')}
      </nav>
      <div id="cic-sidebar-footer">
        cic-test.chronicillnesschannel.co.uk · v2026.04
      </div>`;
  }

  // ─── Mount ────────────────────────────────────────────────────────────────────
  function mount() {
    // Inject styles
    const style = document.createElement('style');
    style.id = 'cic-sidebar-styles';
    style.textContent = CSS;
    document.head.appendChild(style);

    // Sidebar panel
    const aside = document.createElement('aside');
    aside.id = 'cic-sidebar';
    aside.setAttribute('aria-label', 'Site navigation');
    aside.innerHTML = buildSidebar();

    // Overlay (drawer backdrop)
    const overlay = document.createElement('div');
    overlay.id = 'cic-sidebar-overlay';

    // Hamburger button
    const btn = document.createElement('button');
    btn.id = 'cic-hamburger';
    btn.setAttribute('aria-label', 'Toggle navigation');
    btn.innerHTML = `<img src="${location.origin}/images/CIC-hamburger-button.svg" alt="Menu">`;

    // Mount point or body
    const mount = document.getElementById('cic-sidebar-mount');
    if (mount) {
      mount.appendChild(aside);
      mount.appendChild(overlay);
      mount.appendChild(btn);
    } else {
      document.body.prepend(btn);
      document.body.prepend(overlay);
      document.body.prepend(aside);
    }

    // Initial state
    const bp = getBreakpoint();
    if (bp === 'mobile') {
      setState(DRAWER);
    } else if (bp === 'tablet') {
      setState(MINI);
    } else {
      setState(EXPANDED);
      if (!isHomePage()) scheduleAutoCollapse();
    }

    bindEvents(btn, overlay);
  }

  // ─── Events ───────────────────────────────────────────────────────────────────
  function bindEvents(btn, overlay) {
    // Hamburger toggle
    btn.addEventListener('click', () => {
      const bp = getBreakpoint();
      if (bp === 'mobile') {
        document.body.classList.toggle('cic-drawer-open');
      } else {
        clearTimeout(autoCollapseTimer);
        const cur = getState();
        setState(cur === EXPANDED ? COLLAPSED : EXPANDED);
      }
    });

    // Overlay click (close drawer)
    overlay.addEventListener('click', () => {
      document.body.classList.remove('cic-drawer-open');
    });

    // Left-edge hover re-expand (desktop only)
    document.addEventListener('mousemove', e => {
      if (getBreakpoint() !== 'desktop') return;
      if (e.clientX < 20 && getState() === COLLAPSED) {
        clearTimeout(autoCollapseTimer);
        setState(EXPANDED);
      }
    });

    // Cancel auto-collapse if user hovers the sidebar
    document.getElementById('cic-sidebar').addEventListener('mouseenter', () => {
      clearTimeout(autoCollapseTimer);
    });

    // Resume auto-collapse on mouse leave (non-home pages only)
    document.getElementById('cic-sidebar').addEventListener('mouseleave', () => {
      if (!isHomePage() && getBreakpoint() === 'desktop' && getState() === EXPANDED) {
        scheduleAutoCollapse();
      }
    });

    // Section expand/collapse toggles
    document.querySelectorAll('.cic-section-toggle').forEach(toggle => {
      toggle.addEventListener('click', () => {
        const id = toggle.dataset.section;
        if (!id) return;
        const children = document.querySelector(`#cic-section-${id} .cic-section-children`);
        if (!children) return;
        const nowExpanded = toggle.getAttribute('aria-expanded') === 'true';
        toggle.setAttribute('aria-expanded', !nowExpanded);
        children.setAttribute('aria-hidden', nowExpanded);
        sectionExpanded[id] = !nowExpanded;
      });
    });

    // Responsive resize
    window.addEventListener('resize', () => {
      const bp = getBreakpoint();
      if (bp === 'tablet') {
        setState(MINI);
        document.body.classList.remove('cic-drawer-open');
        clearTimeout(autoCollapseTimer);
      } else if (bp === 'mobile') {
        setState(DRAWER);
        clearTimeout(autoCollapseTimer);
      } else {
        // Back to desktop — restore sensible state
        if (getState() === MINI || getState() === DRAWER) {
          document.body.classList.remove('cic-drawer-open');
          setState(isHomePage() ? EXPANDED : COLLAPSED);
        }
      }
    });
  }

  // Run after DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
