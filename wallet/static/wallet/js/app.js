/* ==========================================================================
   TERMINAL WALLET APP
   Dependency-free vanilla JS.
   HTMX-friendly: events and selectors are intentionally generic.
   ========================================================================== */

(function () {
  "use strict";

  /* =========================================================================
     AMOUNT HELPERS
     ========================================================================= */

  const amountFormatter = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    useGrouping: true,
  });

  function parseAmount(value) {
    if (typeof value === "number") {
      return Number.isFinite(value) ? value : 0;
    }

    if (typeof value !== "string") {
      return 0;
    }

    const normalized = value.replace(/,/g, "").trim();

    if (!normalized) {
      return 0;
    }

    const number = Number(normalized);

    return Number.isFinite(number) ? number : 0;
  }

  function formatAmount(value, options) {
    const amount = parseAmount(value);

    const config = {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
      useGrouping: true,
      ...(options || {}),
    };

    return new Intl.NumberFormat("en-US", config).format(amount);
  }

  function formatCurrency(value, currency) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: currency || "USD",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
      useGrouping: true,
    }).format(parseAmount(value));
  }

  window.walletFormatAmount = formatAmount;
  window.walletFormatCurrency = formatCurrency;

  /* =========================================================================
     STATUS BADGE HELPERS
     ========================================================================= */

  const STATUS_CLASS_MAP = {
    PENDING: "status-badge--pending",
    COMPLETED: "status-badge--completed",
    FAILED: "status-badge--failed",
    CANCELLED: "status-badge--cancelled",
  };

  function statusBadgeClass(status) {
    const normalized = String(status || "")
      .trim()
      .toUpperCase();

    return STATUS_CLASS_MAP[normalized] || "status-badge--cancelled";
  }

  function setStatusBadge(element, status) {
    if (!element) {
      return;
    }

    Object.values(STATUS_CLASS_MAP).forEach(function (className) {
      element.classList.remove(className);
    });

    const normalized = String(status || "")
      .trim()
      .toUpperCase();

    element.classList.add(statusBadgeClass(normalized));
    element.textContent = normalized || "UNKNOWN";
    element.dataset.status = normalized;
  }

  window.walletStatusBadgeClass = statusBadgeClass;
  window.walletSetStatusBadge = setStatusBadge;

  /* =========================================================================
     TOASTS
     ========================================================================= */

  function getToastRegion() {
    let region = document.querySelector("[data-toast-region]");

    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.dataset.toastRegion = "";
      region.setAttribute("aria-live", "polite");
      region.setAttribute("aria-atomic", "false");
      document.body.appendChild(region);
    }

    return region;
  }

  function walletToast(message, type) {
    const region = getToastRegion();

    const normalizedType = ["success", "error", "warning", "info"].includes(
      type
    )
      ? type
      : "info";

    const toast = document.createElement("div");
    toast.className = "toast toast--" + normalizedType;
    toast.setAttribute("role", "status");

    const icon = document.createElement("span");
    icon.className = "toast-icon";

    const iconElement = document.createElement("i");

    const iconMap = {
      success: "ti ti-check",
      error: "ti ti-x",
      warning: "ti ti-alert-triangle",
      info: "ti ti-info-circle",
    };

    iconElement.className = iconMap[normalizedType];
    icon.appendChild(iconElement);

    const messageElement = document.createElement("span");
    messageElement.className = "toast-message";
    messageElement.textContent = String(message || "");

    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Dismiss notification");
    close.innerHTML = '<i class="ti ti-x"></i>';

    close.addEventListener("click", function () {
      removeToast(toast);
    });

    toast.appendChild(icon);
    toast.appendChild(messageElement);
    toast.appendChild(close);

    region.appendChild(toast);

    const timeout = window.setTimeout(function () {
      removeToast(toast);
    }, 4000);

    toast.addEventListener("mouseenter", function () {
      window.clearTimeout(timeout);
    });

    return toast;
  }

  function removeToast(toast) {
    if (!toast || !toast.parentNode) {
      return;
    }

    toast.style.opacity = "0";
    toast.style.transform = "translateY(5px)";
    toast.style.transition = "opacity 100ms ease, transform 100ms ease";

    window.setTimeout(function () {
      if (toast.parentNode) {
        toast.parentNode.removeChild(toast);
      }
    }, 110);
  }

  window.walletToast = walletToast;

  /* =========================================================================
     CONFIRMATION HELPER
     ========================================================================= */

  function walletConfirm(message, options) {
    const config = options || {};

    const prompt = config.title
      ? config.title + "\n\n" + message
      : message;

    const confirmed = window.confirm(prompt);

    if (confirmed && typeof config.onConfirm === "function") {
      config.onConfirm();
    }

    return confirmed;
  }

  window.walletConfirm = walletConfirm;

  function bindDestructiveConfirmations(root) {
    const scope = root || document;

    scope.querySelectorAll("[data-confirm]").forEach(function (element) {
      if (element.dataset.confirmBound === "true") {
        return;
      }

      element.dataset.confirmBound = "true";

      element.addEventListener("click", function (event) {
        const message =
          element.dataset.confirm ||
          "Are you sure you want to continue?";

        if (!window.confirm(message)) {
          event.preventDefault();
          event.stopPropagation();
        }
      });
    });
  }

  /* =========================================================================
     COMMAND PALETTE
     ========================================================================= */

  const COMMANDS = [
    {
      action: "transfer",
      label: "Create transfer",
      icon: "ti ti-arrow-up-right",
      shortcut: "T",
    },
    {
      action: "deposit",
      label: "Deposit funds",
      icon: "ti ti-arrow-down-left",
      shortcut: "D",
    },
    {
      action: "withdraw",
      label: "Withdraw funds",
      icon: "ti ti-arrow-up-left",
      shortcut: "W",
    },
    {
      action: "transactions",
      label: "View transactions",
      icon: "ti ti-list-details",
      shortcut: "X",
    },
    {
      action: "overview",
      label: "Go to overview",
      icon: "ti ti-layout-dashboard",
      shortcut: "O",
    },
    {
      action: "admin",
      label: "Open admin",
      icon: "ti ti-shield",
      shortcut: "A",
    },
  ];

  let palette = null;
  let paletteInput = null;
  let paletteList = null;
  let paletteItems = [];
  let selectedIndex = 0;

  function createCommandPalette() {
    if (document.querySelector("[data-command-palette]")) {
      palette = document.querySelector("[data-command-palette]");
      paletteInput = palette.querySelector("[data-command-input]");
      paletteList = palette.querySelector("[data-command-list]");
      return;
    }

    palette = document.createElement("div");
    palette.className = "command-palette-backdrop";
    palette.dataset.commandPalette = "";
    palette.setAttribute("aria-hidden", "true");

    palette.innerHTML = `
      <div
        class="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
      >
        <div class="command-palette-search">
          <i class="ti ti-search"></i>
          <input
            class="command-palette-input"
            type="search"
            placeholder="Search commands..."
            autocomplete="off"
            data-command-input
            aria-label="Search commands"
          />
          <span class="kbd">ESC</span>
        </div>
        <div
          class="command-palette-list"
          data-command-list
          role="listbox"
          aria-label="Commands"
        ></div>
      </div>
    `;

    document.body.appendChild(palette);

    paletteInput = palette.querySelector("[data-command-input]");
    paletteList = palette.querySelector("[data-command-list]");

    palette.addEventListener("mousedown", function (event) {
      if (event.target === palette) {
        closeCommandPalette();
      }
    });

    paletteInput.addEventListener("input", function () {
      renderCommandItems(paletteInput.value);
    });

    paletteInput.addEventListener("keydown", handlePaletteKeydown);

    renderCommandItems("");
  }

  function getFilteredCommands(query) {
    const normalized = String(query || "")
      .trim()
      .toLowerCase();

    if (!normalized) {
      return COMMANDS;
    }

    return COMMANDS.filter(function (command) {
      return (
        command.label.toLowerCase().includes(normalized) ||
        command.action.toLowerCase().includes(normalized)
      );
    });
  }

  function renderCommandItems(query) {
    if (!paletteList) {
      return;
    }

    const commands = getFilteredCommands(query);

    paletteList.innerHTML = "";
    paletteItems = [];
    selectedIndex = 0;

    commands.forEach(function (command, index) {
      const item = document.createElement("div");

      item.className = "command-palette-item";
      item.dataset.action = command.action;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", index === 0 ? "true" : "false");

      item.innerHTML = `
        <i class="${command.icon}"></i>
        <span class="command-palette-item-label"></span>
        <span class="kbd">${command.shortcut}</span>
      `;

      item.querySelector(".command-palette-item-label").textContent =
        command.label;

      item.addEventListener("mouseenter", function () {
        selectedIndex = index;
        updateSelectedCommand();
      });

      item.addEventListener("click", function () {
        runCommand(command.action);
      });

      paletteList.appendChild(item);
      paletteItems.push(item);
    });

    if (commands.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.style.minHeight = "100px";
      empty.innerHTML = `
        <span class="empty-state-icon">
          <i class="ti ti-search-off"></i>
        </span>
        <span class="empty-state-title">No commands found</span>
      `;
      paletteList.appendChild(empty);
    }

    updateSelectedCommand();
  }

  function updateSelectedCommand() {
    paletteItems.forEach(function (item, index) {
      const selected = index === selectedIndex;

      item.classList.toggle("is-selected", selected);
      item.setAttribute("aria-selected", selected ? "true" : "false");

      if (selected) {
        item.scrollIntoView({
          block: "nearest",
        });
      }
    });
  }

  function handlePaletteKeydown(event) {
    if (event.key === "ArrowDown") {
      event.preventDefault();

      if (paletteItems.length) {
        selectedIndex =
          selectedIndex + 1 >= paletteItems.length
            ? 0
            : selectedIndex + 1;

        updateSelectedCommand();
      }
    }

    if (event.key === "ArrowUp") {
      event.preventDefault();

      if (paletteItems.length) {
        selectedIndex =
          selectedIndex - 1 < 0
            ? paletteItems.length - 1
            : selectedIndex - 1;

        updateSelectedCommand();
      }
    }

    if (event.key === "Enter") {
      event.preventDefault();

      const selected = paletteItems[selectedIndex];

      if (selected) {
        runCommand(selected.dataset.action);
      }
    }

    if (event.key === "Escape") {
      event.preventDefault();
      closeCommandPalette();
    }
  }

  function openCommandPalette() {
    createCommandPalette();

    palette.classList.add("is-open");
    palette.setAttribute("aria-hidden", "false");

    paletteInput.value = "";
    renderCommandItems("");

    window.setTimeout(function () {
      paletteInput.focus();
    }, 0);
  }

  function closeCommandPalette() {
    if (!palette) {
      return;
    }

    palette.classList.remove("is-open");
    palette.setAttribute("aria-hidden", "true");
  }

  function runCommand(action) {
    closeCommandPalette();

    document.dispatchEvent(
      new CustomEvent("wallet:action", {
        detail: {
          action: action,
        },
      })
    );
  }

  window.walletOpenCommandPalette = openCommandPalette;
  window.walletCloseCommandPalette = closeCommandPalette;

  /* =========================================================================
     LIVE AMOUNT PREVIEW
     ========================================================================= */

  function updateLiveAmountPreview(input) {
    if (!input) {
      return;
    }

    const currentBalanceElement = document.querySelector(
      "[data-current-balance]"
    );

    const previewElement = document.querySelector(
      '[data-preview="new-balance"]'
    );

    if (!currentBalanceElement || !previewElement) {
      return;
    }

    const currentBalance = parseAmount(
      currentBalanceElement.dataset.currentBalance ||
        currentBalanceElement.value ||
        currentBalanceElement.textContent
    );

    const amount = parseAmount(input.value);

    /*
     * data-live-amount supports:
     *   transfer / withdrawal → subtract
     *   deposit / credit      → add
     *
     * Default is subtract because the primary preview use case
     * is a transfer/withdrawal.
     */
    const operation =
      input.dataset.amountOperation ||
      (input.closest("form") && input.closest("form").dataset.amountOperation) ||
      "subtract";

    const newBalance =
      operation === "add"
        ? currentBalance + amount
        : currentBalance - amount;

    previewElement.textContent = formatAmount(newBalance);

    /*
     * Keep the value width stable even when the number of digits changes.
     * CSS min-height also prevents vertical layout changes.
     */
    previewElement.setAttribute(
      "aria-label",
      "New balance " + formatCurrency(newBalance)
    );
  }

  function bindLiveAmountPreview(root) {
    const scope = root || document;

    scope.querySelectorAll("[data-live-amount]").forEach(function (input) {
      if (input.dataset.liveAmountBound === "true") {
        updateLiveAmountPreview(input);
        return;
      }

      input.dataset.liveAmountBound = "true";

      input.addEventListener("input", function () {
        updateLiveAmountPreview(input);
      });

      input.addEventListener("change", function () {
        updateLiveAmountPreview(input);
      });

      updateLiveAmountPreview(input);
    });
  }

  /* =========================================================================
     FILTER CHIPS
     ========================================================================= */

  function bindFilterChips(root) {
    const scope = root || document;

    scope.querySelectorAll("[data-filter]").forEach(function (chip) {
      if (chip.dataset.filterBound === "true") {
        return;
      }

      chip.dataset.filterBound = "true";

      chip.addEventListener("click", function () {
        const group = chip.closest("[data-filter-group]") || scope;
        const filter = chip.dataset.filter;

        group.querySelectorAll("[data-filter]").forEach(function (item) {
          item.classList.remove("active");
        });

        chip.classList.add("active");

        document.dispatchEvent(
          new CustomEvent("wallet:filter", {
            detail: {
              filter: filter,
            },
          })
        );
      });
    });
  }

  /* =========================================================================
     MODALS
     ========================================================================= */

  function openModal(modalOrSelector) {
    const modal =
      typeof modalOrSelector === "string"
        ? document.querySelector(modalOrSelector)
        : modalOrSelector;

    if (!modal) {
      return;
    }

    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");

    const focusTarget = modal.querySelector(
      "[autofocus], .modal-close, button, input, select, textarea"
    );

    if (focusTarget) {
      window.setTimeout(function () {
        focusTarget.focus();
      }, 0);
    }
  }

  function closeModal(modalOrSelector) {
    const modal =
      typeof modalOrSelector === "string"
        ? document.querySelector(modalOrSelector)
        : modalOrSelector;

    if (!modal) {
      return;
    }

    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
  }

  function bindModals(root) {
    const scope = root || document;

    scope.querySelectorAll("[data-modal-open]").forEach(function (trigger) {
      if (trigger.dataset.modalBound === "true") {
        return;
      }

      trigger.dataset.modalBound = "true";

      trigger.addEventListener("click", function () {
        openModal(trigger.dataset.modalOpen);
      });
    });

    scope.querySelectorAll("[data-modal-close]").forEach(function (trigger) {
      if (trigger.dataset.modalBound === "true") {
        return;
      }

      trigger.dataset.modalBound = "true";

      trigger.addEventListener("click", function () {
        const modal = trigger.closest(".modal-backdrop");

        if (modal) {
          closeModal(modal);
        }
      });
    });

    scope.querySelectorAll(".modal-backdrop").forEach(function (backdrop) {
      if (backdrop.dataset.backdropBound === "true") {
        return;
      }

      backdrop.dataset.backdropBound = "true";

      backdrop.addEventListener("mousedown", function (event) {
        if (event.target === backdrop) {
          closeModal(backdrop);
        }
      });
    });
  }

  /* =========================================================================
     GLOBAL KEYBOARD HANDLERS
     ========================================================================= */

  function isTypingContext(element) {
    if (!element) {
      return false;
    }

    const tag = element.tagName;

    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      element.isContentEditable
    );
  }

  function bindGlobalKeyboard() {
    document.addEventListener("keydown", function (event) {
      const modifier = event.metaKey || event.ctrlKey;

      if (modifier && event.key.toLowerCase() === "k") {
        event.preventDefault();
        openCommandPalette();
        return;
      }

      if (event.key === "Escape") {
        if (palette && palette.classList.contains("is-open")) {
          closeCommandPalette();
          return;
        }

        const openModalElement = document.querySelector(
          ".modal-backdrop.is-open"
        );

        if (openModalElement) {
          closeModal(openModalElement);
        }
      }

      /*
       * Optional single-letter shortcuts are intentionally disabled while
       * the user is typing.
       */
      if (!isTypingContext(document.activeElement)) {
        const command = COMMANDS.find(function (item) {
          return item.shortcut.toLowerCase() === event.key.toLowerCase();
        });

        if (event.key.length === 1 && command) {
          /*
           * Leave single-letter shortcuts opt-in via data-command-shortcuts.
           */
          if (document.body.dataset.commandShortcuts === "true") {
            runCommand(command.action);
          }
        }
      }
    });
  }

  /* =========================================================================
     HTMX-FRIENDLY INITIALIZATION
     ========================================================================= */

  function initWalletUI(root) {
    const scope = root || document;

    bindLiveAmountPreview(scope);
    bindDestructiveConfirmations(scope);
    bindFilterChips(scope);
    bindModals(scope);
  }

  window.walletInit = initWalletUI;

  document.addEventListener("DOMContentLoaded", function () {
    createCommandPalette();
    bindGlobalKeyboard();
    initWalletUI(document);
  });

  /*
   * If HTMX is present later, newly swapped content gets the same bindings.
   * The listener is harmless on the current static page.
   */
  document.addEventListener("htmx:afterSwap", function (event) {
    initWalletUI(event.target);
  });

  document.addEventListener("htmx:afterSettle", function (event) {
    initWalletUI(event.target);
  });
})();
