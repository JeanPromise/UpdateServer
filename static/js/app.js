(() => {
  const navToggle = document.querySelector('[data-nav-toggle]');
  const nav = document.querySelector('[data-nav]');
  if (navToggle && nav) {
    navToggle.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      navToggle.setAttribute('aria-expanded', String(open));
    });
    nav.querySelectorAll('a').forEach((link) => link.addEventListener('click', () => {
      nav.classList.remove('open');
      navToggle.setAttribute('aria-expanded', 'false');
    }));
  }

  document.querySelectorAll('[data-dismiss]').forEach((button) => {
    button.addEventListener('click', () => button.closest('.flash')?.remove());
  });

  document.querySelectorAll('[data-confirm]').forEach((element) => {
    element.addEventListener('click', (event) => {
      const message = element.getAttribute('data-confirm') || 'Continue?';
      if (!window.confirm(message)) event.preventDefault();
    });
  });

  async function copyText(text, button) {
    try {
      await navigator.clipboard.writeText(text);
      const oldText = button.textContent;
      button.textContent = 'Copied';
      window.setTimeout(() => { button.textContent = oldText; }, 1600);
    } catch (_) {
      const temporary = document.createElement('textarea');
      temporary.value = text;
      temporary.style.position = 'fixed';
      temporary.style.opacity = '0';
      document.body.appendChild(temporary);
      temporary.select();
      try { document.execCommand('copy'); } finally { temporary.remove(); }
      const oldText = button.textContent;
      button.textContent = 'Copied';
      window.setTimeout(() => { button.textContent = oldText; }, 1600);
    }
  }
  document.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = document.getElementById(button.getAttribute('data-copy-target'));
      if (target) copyText(target.textContent.trim(), button);
    });
  });
  document.querySelectorAll('[data-copy]').forEach((button) => {
    button.addEventListener('click', () => copyText(button.getAttribute('data-copy') || '', button));
  });
  document.querySelectorAll('[data-copy-code]').forEach((button) => {
    button.addEventListener('click', () => {
      const block = button.closest('.code-block')?.querySelector('pre code');
      if (block) copyText(block.textContent, button);
    });
  });

  const offlinePill = document.querySelector('[data-offline-pill]');
  const updateConnection = () => {
    if (offlinePill) offlinePill.hidden = navigator.onLine;
  };
  window.addEventListener('online', updateConnection);
  window.addEventListener('offline', updateConnection);
  updateConnection();

  let deferredPrompt = null;
  const installButton = document.querySelector('[data-install-pwa]');
  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    deferredPrompt = event;
    if (installButton) installButton.hidden = false;
  });
  if (installButton) {
    installButton.addEventListener('click', async () => {
      if (!deferredPrompt) {
        installButton.textContent = 'Open browser menu to install';
        return;
      }
      deferredPrompt.prompt();
      await deferredPrompt.userChoice;
      deferredPrompt = null;
      installButton.hidden = true;
    });
  }
  window.addEventListener('appinstalled', () => {
    if (installButton) installButton.hidden = true;
  });

  if ('serviceWorker' in navigator && location.protocol === 'https:') {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }
})();
