import {
  useEffect,
} from "react";

import {
  useBackendHealth,
  useChatActions,
  useConversationList,
} from "./hooks/use-chat";

import { useChatStore } from "./stores/chat-store";

import {
  Sidebar,
} from "./components/Sidebar";

import {
  ChatView,
} from "./components/ChatView";

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

  const conversations =
    useConversationList();

  const health =
    useBackendHealth();

  const actions =
    useChatActions();

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
        event.key === "Escape"
      ) {
        void actions.cancel();
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
  }, [actions]);

  useEffect(() => {
    if (
      activeId &&
      conversations.data &&
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
    setActive,
  ]);

  return (
    <div className="app-shell">
      <Sidebar
        conversations={
          conversations.data
        }
        activeId={activeId}
        backendOnline={
          health.isSuccess
        }
        onNew={
          actions.newChat
        }
        onSelect={
          setActive
        }
      />

      <ChatView />
    </div>
  );
}