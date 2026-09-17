import {
  useEffect,
  useMemo,
} from "react";

import {
  useBackendHealth,
  useChatActions,
  useConversationList,
} from "./hooks/use-chat";

import {
  useChatStore,
} from "./stores/chat-store";

import {
  Sidebar,
} from "./components/Sidebar";

import {
  ChatView,
} from "./components/ChatView";

import {
  SettingsModal,
} from "./components/SettingsModal";

export default function App() {
  const activeId =
    useChatStore(
      (state) =>
        state.activeConversationId,
    );

  const setActive =
    useChatStore(
      (state) =>
        state.setActiveConversation,
    );

  const sidebarOpen =
    useChatStore(
      (state) =>
        state.sidebarOpen,
    );

  const toggleSidebar =
    useChatStore(
      (state) =>
        state.toggleSidebar,
    );

  const theme =
    useChatStore(
      (state) =>
        state.theme,
    );

  const setTheme =
    useChatStore(
      (state) =>
        state.setTheme,
    );

  const showsSettings =
    useChatStore(
      (state) =>
        state.showsSettings,
    );

  const setShowsSettings =
    useChatStore(
      (state) =>
        state.setShowsSettings,
    );

  const setMemoryPanelOpen =
    useChatStore(
      (state) =>
        state.setMemoryPanelOpen,
    );

  const conversations =
    useConversationList();

  const health =
    useBackendHealth();

  const actions =
    useChatActions();

  // Resolve "system" to a concrete light/dark value
  const resolvedTheme = useMemo(() => {
    if (theme !== "system") {
      return theme;
    }

    if (
      typeof window !==
        "undefined" &&
      window.matchMedia
    ) {
      return window.matchMedia(
        "(prefers-color-scheme: dark)",
      ).matches
        ? "dark"
        : "light";
    }

    return "light";
  }, [theme]);

  useEffect(() => {
    document.documentElement.setAttribute(
      "data-theme",
      resolvedTheme,
    );
  }, [resolvedTheme]);

  useEffect(() => {
    function keydown(
      event: KeyboardEvent,
    ) {
      if (
        (event.metaKey ||
          event.ctrlKey) &&
        event.key.toLowerCase() ===
          "n"
      ) {
        event.preventDefault();

        actions.newChat();
      }

      if (
        (event.metaKey ||
          event.ctrlKey) &&
        event.key.toLowerCase() ===
          "b"
      ) {
        event.preventDefault();

        toggleSidebar();
      }
    }

    window.addEventListener(
      "keydown",
      keydown,
    );

    return () =>
      window.removeEventListener(
        "keydown",
        keydown,
      );
  }, [actions, toggleSidebar]);

  useEffect(() => {
    if (
      activeId &&
      conversations.data &&
      !conversations.isFetching &&
      !conversations.data.some(
        (conversation) =>
          conversation.id ===
          activeId,
      )
    ) {
      setActive(null);
    }
  }, [
    activeId,
    conversations.data,
    conversations.isFetching,
    setActive,
  ]);

  return (
    <div
      className={`app-shell ${
        sidebarOpen
          ? ""
          : "app-shell--sidebar-hidden"
      }`}>

      <Sidebar
        conversations={
          conversations.data
        }
        activeId={activeId}
        onNew={
          actions.newChat
        }
        onSelect={
          setActive
        }
        onDelete={
          actions.deleteConversation
        }
        onOpenMemory={() =>
          setMemoryPanelOpen(true)
        }
        onOpenSettings={() =>
          setShowsSettings(true)
        }
      />

      <ChatView
        sidebarOpen={
          sidebarOpen
        }
        onToggleSidebar={
          toggleSidebar
        }
      />

      <SettingsModal
        open={showsSettings}
        theme={theme}
        backendOnline={
          health.isSuccess
        }
        onClose={() =>
          setShowsSettings(false)
        }
        onSetTheme={setTheme}
      />
    </div>
  );
}
