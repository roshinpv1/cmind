"""
Session store — in-memory conversation memory.

Tracks conversation history per session for multi-turn agent interactions.
"""

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field


@dataclass
class Message:
    """A single conversation message."""
    role: str  # "user", "assistant", "system", "tool"
    content: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict = field(default_factory=dict)


@dataclass
class Session:
    """A conversation session."""
    session_id: str
    repo_id: str
    messages: list[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    tool_budget_used: int = 0
    tool_budget_max: int = 20


class SessionStore:
    """In-memory session store with LRU eviction.
    
    Manages conversation history for multi-turn agent interactions.
    Sessions auto-expire after max_sessions limit (LRU eviction).
    """

    def __init__(self, max_sessions: int = 100, max_messages_per_session: int = 50):
        """
        Initialize session store.

        Args:
            max_sessions: Maximum concurrent sessions (LRU eviction)
            max_messages_per_session: Maximum messages per session (older trimmed)
        """
        self.max_sessions = max_sessions
        self.max_messages = max_messages_per_session
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    def create_session(self, repo_id: str, session_id: str | None = None) -> Session:
        """Create a new session."""
        sid = session_id or str(uuid.uuid4())
        session = Session(session_id=sid, repo_id=repo_id)
        self._sessions[sid] = session
        self._evict_if_needed()
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get session by ID, moving to end (LRU touch)."""
        if session_id in self._sessions:
            self._sessions.move_to_end(session_id)
            return self._sessions[session_id]
        return None

    def add_message(self, session_id: str, role: str, content: str,
                    metadata: dict | None = None) -> Message | None:
        """Add a message to a session."""
        session = self.get_session(session_id)
        if not session:
            return None

        msg = Message(role=role, content=content, metadata=metadata or {})
        session.messages.append(msg)
        session.last_active = time.time()

        # Trim old messages
        if len(session.messages) > self.max_messages:
            # Keep system messages + most recent
            system_msgs = [m for m in session.messages if m.role == "system"]
            other_msgs = [m for m in session.messages if m.role != "system"]
            keep_count = self.max_messages - len(system_msgs)
            session.messages = system_msgs + other_msgs[-keep_count:]

        return msg

    def get_history(self, session_id: str, last_n: int | None = None) -> list[Message]:
        """Get conversation history for a session."""
        session = self.get_session(session_id)
        if not session:
            return []

        messages = session.messages
        if last_n:
            messages = messages[-last_n:]
        return messages

    def get_history_as_text(self, session_id: str, last_n: int = 10) -> str:
        """Get conversation history formatted as text."""
        messages = self.get_history(session_id, last_n)
        lines = []
        for msg in messages:
            prefix = {"user": "User", "assistant": "Assistant", "tool": "Tool", "system": "System"}.get(msg.role, msg.role)
            # Truncate long messages
            content = msg.content[:500] + "..." if len(msg.content) > 500 else msg.content
            lines.append(f"[{prefix}]: {content}")
        return "\n".join(lines)

    def increment_tool_budget(self, session_id: str) -> bool:
        """Increment tool budget counter. Returns False if budget exhausted."""
        session = self.get_session(session_id)
        if not session:
            return False
        session.tool_budget_used += 1
        return session.tool_budget_used <= session.tool_budget_max

    def delete_session(self, session_id: str):
        """Delete a session."""
        self._sessions.pop(session_id, None)

    def list_sessions(self) -> list[dict]:
        """List all active sessions."""
        return [
            {
                "session_id": s.session_id,
                "repo_id": s.repo_id,
                "messages": len(s.messages),
                "created_at": s.created_at,
                "last_active": s.last_active,
            }
            for s in self._sessions.values()
        ]

    def _evict_if_needed(self):
        """Evict oldest sessions if over limit."""
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)
