import React from "react";

export const HEALTH_ENDPOINT = "/api/health";
export const LOCAL_SESSION_ENDPOINT = "/api/session";
export const NAVIGATION_DESTINATIONS = [
  { id: "workbench", label: "工作台", emptyState: "尚未选择 vault。" },
  { id: "materials", label: "资料", emptyState: "当前没有已授权的 vault。" },
  { id: "sessions", label: "会话", emptyState: "当前没有已保存的会话。" },
  { id: "tasks", label: "任务", emptyState: "当前没有任务。" },
  { id: "settings", label: "设置", emptyState: "当前没有可用设置。" }
];

function NavigationLinks({ activeDestination, firstLinkRef, onNavigate }) {
  return NAVIGATION_DESTINATIONS.map((destination, index) =>
    React.createElement(
      "a",
      {
        className: "navigation-link",
        href: `#${destination.id}`,
        key: destination.id,
        ref: index === 0 ? firstLinkRef : undefined,
        "aria-current": activeDestination === destination.id ? "page" : undefined,
        onClick: (event) => {
          event.preventDefault();
          onNavigate(destination.id);
        }
      },
      destination.label
    )
  );
}

export function App() {
  const [activeDestination, setActiveDestination] = React.useState("workbench");
  const [healthStatus, setHealthStatus] = React.useState("本机服务正在验证");
  const [sessionStatus, setSessionStatus] = React.useState("本机会话正在建立");
  const [menuOpen, setMenuOpen] = React.useState(false);
  const menuButtonRef = React.useRef(null);
  const firstMenuLinkRef = React.useRef(null);
  const menuPanelRef = React.useRef(null);

  React.useEffect(() => {
    fetch(HEALTH_ENDPOINT)
      .then((response) => {
        if (!response.ok) throw new Error("Health check failed.");
        return response.json();
      })
      .then(() => setHealthStatus("本机服务可用"))
      .catch(() => setHealthStatus("本机服务不可用"));

    fetch(LOCAL_SESSION_ENDPOINT)
      .then((response) => {
        if (!response.ok) throw new Error("Local session check failed.");
        return response.json();
      })
      .then(() => setSessionStatus("本机会话已建立"))
      .catch(() => setSessionStatus("本机会话不可用"));
  }, []);

  React.useEffect(() => {
    if (menuOpen) firstMenuLinkRef.current?.focus();
  }, [menuOpen]);

  const activePage = NAVIGATION_DESTINATIONS.find(
    (destination) => destination.id === activeDestination
  );

  function closeMenu() {
    setMenuOpen(false);
    menuButtonRef.current?.focus();
  }

  function navigate(destinationId) {
    setActiveDestination(destinationId);
    if (menuOpen) closeMenu();
  }

  function handleMenuKeyDown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeMenu();
      return;
    }
    if (event.key !== "Tab") return;

    const focusable = [...menuPanelRef.current.querySelectorAll('a[href], button:not([disabled])')];
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return React.createElement(
    "div",
    { className: "app-shell" },
    React.createElement(
      "aside",
      { className: "navigation-rail", "aria-label": "主导航" },
      React.createElement("div", { className: "brand" }, "本机知识工作台"),
      React.createElement(
        "nav",
        { "aria-label": "工作区目的地" },
        React.createElement(NavigationLinks, {
          activeDestination,
          onNavigate: navigate
        })
      ),
      React.createElement("p", { className: "rail-status" }, "仅限本机访问")
    ),
    React.createElement(
      "section",
      { className: "application-content" },
      React.createElement(
        "header",
        { className: "context-bar" },
        React.createElement(
          "button",
          {
            className: "menu-button",
            type: "button",
            ref: menuButtonRef,
            title: "打开导航",
            "aria-label": "打开导航",
            "aria-controls": "mobile-navigation-panel",
            "aria-expanded": menuOpen,
            onClick: () => setMenuOpen(true)
          },
          "☰"
        ),
        React.createElement("p", { className: "context-location" }, "本机 / 当前工作区"),
        React.createElement(
          "div",
          { className: "context-statuses", "aria-live": "polite" },
          React.createElement("span", { "data-testid": "health-status" }, healthStatus),
          React.createElement("span", { "data-testid": "session-status" }, sessionStatus)
        )
      ),
      React.createElement(
        "main",
        { className: "workspace", "aria-labelledby": "workspace-title" },
        React.createElement(
          "div",
          { className: "workspace-inner" },
          React.createElement("p", { className: "eyebrow" }, "本机工作区"),
          React.createElement("h1", { id: "workspace-title" }, activePage.label),
          React.createElement(
            "section",
            { className: "workspace-section", "aria-label": `${activePage.label}状态` },
            React.createElement("p", { className: "section-label" }, "当前状态"),
            React.createElement("p", { className: "empty-state" }, activePage.emptyState)
          )
        )
      )
    ),
    React.createElement(
      "div",
      { className: "navigation-overlay", hidden: !menuOpen },
      React.createElement(
        "aside",
        {
          className: "navigation-panel",
          id: "mobile-navigation-panel",
          ref: menuPanelRef,
          role: "dialog",
          "aria-label": "主导航",
          "aria-modal": "true",
          onKeyDown: handleMenuKeyDown
        },
        React.createElement("p", { className: "brand" }, "本机知识工作台"),
        React.createElement(
          "nav",
          { "aria-label": "工作区目的地" },
          React.createElement(NavigationLinks, {
            activeDestination,
            firstLinkRef: firstMenuLinkRef,
            onNavigate: navigate
          })
        ),
        React.createElement(
          "button",
          {
            className: "panel-close",
            type: "button",
            onClick: closeMenu
          },
          "关闭"
        )
      )
    )
  );
}
