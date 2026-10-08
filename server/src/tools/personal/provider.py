"""Trajecta's personal-agent tool registry.

Deep Agents already provides the narrow harness primitives (ls/read_file/
write_file/edit_file/delete/glob/grep, task, and sandbox ``execute`` when the
backend supports it).  This provider deliberately does not duplicate them.  It
adds the personal-agent layer: memory, sessions, tasks, schedules, web/browser,
documents/media, desktop control, databases, notifications and durable skill
candidates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepagents import FilesystemPermission
from langchain_core.tools import BaseTool, ToolException, tool

from server.src.config import Settings
from server.src.guardrails.policy import (
    PermissionSnapshot,
    TOOL_PERMISSION_GROUPS,
)
from server.src.memory.provider import MemoryProvider
from server.src.tools.personal.browser import BrowserManager
from server.src.tools.personal.computer import ComputerManager
from server.src.tools.personal.database import DatabaseTools
from server.src.tools.personal.documents import DocumentTools
from server.src.tools.personal.media import MediaTools
from server.src.tools.personal.network import NetworkTools
from server.src.tools.personal.notifications import NotificationService
from server.src.tools.personal.process import ProcessManager
from server.src.tools.personal.store import PersonalAgentStore
from server.src.tools.personal.system import SystemTools
from server.src.tools.personal.utility import UtilityTools


class PersonalToolProvider:
    def __init__(
        self,
        settings: Settings,
        memory: MemoryProvider,
        *,
        workspace_root: str | None = None,
        _shared: dict[str, Any] | None = None,
    ) -> None:
        if memory.sqlite is None:
            raise RuntimeError("MemoryProvider must be open before PersonalToolProvider")
        self.settings = settings
        self.memory = memory

        # Components that resolve against a host folder. They are rebuilt per
        # workspace because each one embeds the directory it operates in.
        self._workspace_root = workspace_root or settings.tools.workspace_root

        if _shared is None:
            # The root provider owns everything that is not folder-bound and
            # hands the same objects to every workspace-scoped clone, so a
            # second conversation does not open a second HTTP/browser stack.
            self.store = PersonalAgentStore(memory.sqlite)
            self.network = NetworkTools(settings)
            self.system = SystemTools()
            self.utility = UtilityTools()
            self.notifications = NotificationService(self.store)
            self._shared: dict[str, Any] = {
                "store": self.store,
                "network": self.network,
                "system": self.system,
                "utility": self.utility,
                "notifications": self.notifications,
            }
            self._clones: dict[str, PersonalToolProvider] = {}
            self._root_provider: PersonalToolProvider = self
            self._shared["root"] = self
        else:
            self._shared = _shared
            self.store = _shared["store"]
            self.network = _shared["network"]
            self.system = _shared["system"]
            self.utility = _shared["utility"]
            self.notifications = _shared["notifications"]
            self._clones = {}
            self._root_provider = _shared["root"]

        self.documents = DocumentTools(settings, self._workspace_root)
        self.processes = ProcessManager(self._workspace_root, settings.chat.uploads_path)
        self.browser = BrowserManager(settings, workspace_root=self._workspace_root)
        self.computer = ComputerManager(settings, workspace_root=self._workspace_root)
        self.database = DatabaseTools(self._workspace_root, settings.chat.uploads_path)
        self.media = MediaTools(self._workspace_root, settings.chat.uploads_path)
        self._tools: list[BaseTool] | None = None

    # -- per-conversation binding ----------------------------------------

    def for_workspace(self, workspace_root: str) -> PersonalToolProvider:
        """Return the provider bound to ``workspace_root``.

        Two conversations pointing at different folders get different
        providers, so `/workspace/` resolves to the right directory in each.
        The shared components are passed through untouched — nothing here
        mutates ``settings``, which would leak one conversation's folder into
        another that is running at the same time.
        """
        normalized = str(Path(workspace_root).resolve())
        root = self._root_provider

        if normalized == str(Path(root._workspace_root).resolve()):
            return root

        existing = root._clones.get(normalized)
        if existing is not None:
            return existing

        clone = PersonalToolProvider(
            root.settings,
            root.memory,
            workspace_root=normalized,
            _shared=root._shared,
        )
        root._clones[normalized] = clone
        return clone

    @property
    def workspace_root(self) -> str:
        return self._workspace_root

    def permissions(self, policy: PermissionSnapshot) -> list[FilesystemPermission]:
        """Permissions for Deep Agents' built-in filesystem + execution tools.

        Custom/MCP/browser/computer tools are outside this permission system and
        are controlled by HITL ``interrupt_on`` plus the deterministic policy's
        ``filter_tools``. The workspace write permission is derived from the
        ``filesystem-write`` policy mode so settings changes take effect.
        """
        permissions = [
            FilesystemPermission(operations=["write"], paths=["/uploads/**"], mode="deny"),
            FilesystemPermission(operations=["write"], paths=["/skills/**"], mode="deny"),
        ]

        workspace_mode = policy.mode("filesystem-write")

        if workspace_mode == "deny":
            permissions.append(
                FilesystemPermission(operations=["write"], paths=["/workspace/**"], mode="deny")
            )
        elif workspace_mode == "ask" and self.settings.guardrails.hitl_enabled:
            permissions.append(
                FilesystemPermission(operations=["write"], paths=["/workspace/**"], mode="interrupt")
            )
        # allow -> no explicit workspace rule; Deep Agents grants write freely.

        return permissions

    def filter_tools(self, tools: list[BaseTool], policy: PermissionSnapshot) -> list[BaseTool]:
        """Drop tools whose permission category is DENY.

        DENY removes the tool completely — the model cannot even see it. This is
        the deterministic-policy half of tool surface control (the other half is
        MCP tool enable/disable preferences).
        """

        result: list[BaseTool] = []

        for tool in tools:
            category = TOOL_PERMISSION_GROUPS.get(tool.name)

            if category is not None and policy.mode(category) == "deny":
                continue

            result.append(tool)

        return result

    def interrupt_on(self, policy: PermissionSnapshot) -> dict[str, Any] | None:
        """Risk-based Deep Agents HITL policy for custom action tools.

        Tools belonging to a permission category set to ``ask`` require explicit
        approval before execution. Always-sensitive destructive application
        actions (memory/task/schedule deletion) and ``request_approval`` always
        interrupt regardless of category.
        """
        # Asking a question is a conversational pause, not a permission
        # decision; it must work independently of the action-HITL toggle.
        if not self.settings.guardrails.hitl_enabled:
            return {"ask_user": {"allowed_decisions": ["respond"]}}

        approve_edit_reject = {"allowed_decisions": ["approve", "edit", "reject"]}
        approve_reject = {"allowed_decisions": ["approve", "reject"]}

        policy_result: dict[str, Any] = {
            tool_name: approve_edit_reject
            for tool_name, category in TOOL_PERMISSION_GROUPS.items()
            if policy.mode(category) == "ask"
        }

        policy_result.update(
            {name: approve_reject for name in {"memory_forget", "task_delete", "schedule_delete", "request_approval"}}
        )
        policy_result["ask_user"] = {"allowed_decisions": ["respond"]}

        return policy_result or None

    def get_tools(self) -> list[BaseTool]:
        if self._tools is not None:
            return list(self._tools)
        store = self.store
        memory = self.memory
        network = self.network
        documents = self.documents
        system = self.system
        utility = self.utility
        processes = self.processes
        browser = self.browser
        computer = self.computer
        database = self.database
        media = self.media
        notifications = self.notifications

        # ---- Human interaction ---------------------------------------
        @tool
        def ask_user(
            question: str,
            options: list[str] | None = None,
            allow_multiple: bool = False,
        ) -> str:
            """Ask the human a blocking clarification. Options become selectable answers. Never call for information already supplied."""
            # With HITL enabled, `interrupt_on` pauses before this function and
            # the user's `respond` decision becomes the synthetic tool result.
            # This fallback only applies to explicitly non-interactive runs.
            rendered = ", ".join(options or [])
            return f"User interaction unavailable. Question: {question}; options: {rendered}; multiple={allow_multiple}"

        @tool
        def request_approval(action_description: str) -> str:
            """Pause for explicit user approval before a sensitive action not already covered by another tool policy."""
            return f"Approved action: {action_description}"

        # ---- Memory / session recall ---------------------------------
        @tool
        async def memory_search(query: str, limit: int = 10) -> list[dict[str, Any]]:
            """Search durable semantic memories from previous work and user preferences."""
            return await memory.semantic.asearch(query, limit=limit)

        @tool
        async def memory_read(key: str) -> str | None:
            """Read one durable semantic memory by its key."""
            return await memory.semantic.aget(key)

        @tool
        async def memory_save(key: str, content: str) -> str:
            """Save or replace a durable user/project fact for future sessions."""
            await memory.semantic.aput(key, content)
            return f"Saved memory {key!r}"

        @tool
        async def memory_forget(key: str) -> str:
            """Delete one durable semantic memory by key."""
            await memory.semantic.adelete(key)
            return f"Deleted memory {key!r}"

        @tool
        async def session_search(query: str, limit: int = 20) -> list[dict[str, Any]]:
            """Search prior Trajecta conversations by their actual message text/title."""
            return await store.search_sessions(query, limit=limit)

        @tool
        async def session_read(conversation_id: str, limit: int = 100) -> dict[str, Any] | None:
            """Read the message history of a previous Trajecta conversation."""
            return await store.read_session(conversation_id, limit=limit)

        # ---- Verified-skill inputs / trajectory lookup ---------------
        @tool
        async def skill_list() -> list[str]:
            """List verified/promoted procedural skills available to Trajecta."""
            return await memory.procedural.alist()

        @tool
        async def skill_search(query: str, limit: int = 10) -> list[dict[str, str]]:
            """Search verified/promoted procedural skills."""
            return await memory.procedural.asearch(query, limit=limit)

        @tool
        async def skill_view(name: str) -> str | None:
            """Read one verified skill's SKILL.md content."""
            return await memory.procedural.aload(name)

        @tool
        async def skill_candidate_create(
            name: str,
            description: str,
            content: str,
            source_trajectory_ids: list[str] | None = None,
        ) -> dict[str, Any]:
            """Create an unverified candidate skill. This does NOT promote it for use."""
            return await store.create_skill_candidate(
                name=name,
                description=description,
                content=content,
                source_trajectory_ids=source_trajectory_ids,
            )

        @tool
        async def skill_candidate_list(status: str | None = None) -> list[dict[str, Any]]:
            """List candidate skills awaiting/testing verification."""
            return await store.list_skill_candidates(status=status)

        @tool
        async def skill_candidate_update(
            candidate_id: str,
            description: str | None = None,
            content: str | None = None,
            status: str | None = None,
        ) -> dict[str, Any]:
            """Update an unverified candidate skill or its evaluation status."""
            return await store.update_skill_candidate(
                candidate_id,
                description=description,
                content=content,
                status=status,
            )

        @tool
        async def trajectory_search(outcome: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
            """Search recorded task trajectories by outcome for learning/review."""
            return await store.search_trajectories(outcome=outcome, limit=limit)

        # ---- Persistent personal tasks / schedules --------------------
        @tool
        async def task_create(
            title: str,
            notes: str = "",
            due_at: str | None = None,
            priority: str = "normal",
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Create a persistent personal task, then independently read it back."""
            created = await store.create_task(title=title, notes=notes, due_at=due_at, priority=priority, tags=tags)

            task_id = str(created["id"])

            readback = await store.get_task(task_id)

            if readback is None:
                return {
                    "ok": False,
                    "verification_status": "verification_failed",
                    "task_id": task_id,
                    "message": "Task insert returned, but independent SQLite readback failed.",
                }

            verified = (
                readback.get("title") == title.strip()
                and readback.get("priority") == priority
            )

            return {
                "ok": verified,
                "verification_status": "verified" if verified else "verification_failed",
                "task_id": task_id,
                "readback": readback,
            }

        @tool
        async def task_list(status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
            """List persistent personal tasks, optionally filtered by status."""
            return await store.list_tasks(status=status, limit=limit)

        @tool
        async def task_update(
            task_id: str,
            title: str | None = None,
            notes: str | None = None,
            status: str | None = None,
            priority: str | None = None,
            due_at: str | None = None,
            clear_due_at: bool = False,
            tags: list[str] | None = None,
        ) -> dict[str, Any]:
            """Update a persistent personal task, then independently read it back."""
            updated = await store.update_task(
                task_id,
                title=title,
                notes=notes,
                status=status,
                priority=priority,
                due_at=due_at,
                clear_due_at=clear_due_at,
                tags=tags,
            )

            readback = await store.get_task(task_id)

            if readback is None:
                return {
                    "ok": False,
                    "verification_status": "verification_failed",
                    "task_id": task_id,
                }

            checks: list[bool] = []

            if title is not None:
                checks.append(readback["title"] == title)
            if notes is not None:
                checks.append(readback["notes"] == notes)
            if status is not None:
                checks.append(readback["status"] == status)
            if priority is not None:
                checks.append(readback["priority"] == priority)
            if tags is not None:
                checks.append(readback["tags"] == tags)

            if clear_due_at:
                checks.append(readback["due_at"] is None)
            elif due_at is not None:
                checks.append(readback["due_at"] == due_at)

            verified = all(checks) if checks else True

            return {
                "ok": verified,
                "verification_status": "verified" if verified else "verification_failed",
                "task_id": task_id,
                "readback": readback,
            }

        @tool
        async def task_delete(task_id: str) -> dict[str, Any]:
            """Delete a persistent personal task, then independently confirm it is gone."""
            deleted = await store.delete_task(task_id)

            readback = await store.get_task(task_id)

            verified = deleted and readback is None

            return {
                "ok": verified,
                "verification_status": "verified" if verified else "verification_failed",
                "task_id": task_id,
                "exists_after_delete": readback is not None,
            }

        @tool
        async def schedule_create(
            name: str,
            prompt: str,
            schedule_type: str,
            schedule_expr: str,
            timezone: str = "UTC",
        ) -> dict[str, Any]:
            """Schedule future autonomous agent work. type=once|interval|cron; expr=ISO datetime, seconds, or cron expression."""
            return await store.create_schedule(
                name=name,
                prompt=prompt,
                schedule_type=schedule_type,
                schedule_expr=schedule_expr,
                timezone=timezone,
            )

        @tool
        async def schedule_list(enabled_only: bool = False) -> list[dict[str, Any]]:
            """List scheduled autonomous jobs and their next run time."""
            return await store.list_schedules(enabled_only=enabled_only)

        @tool
        async def schedule_update(
            job_id: str,
            name: str | None = None,
            prompt: str | None = None,
            schedule_type: str | None = None,
            schedule_expr: str | None = None,
            timezone: str | None = None,
            enabled: bool | None = None,
        ) -> dict[str, Any]:
            """Modify, pause, or resume an existing scheduled agent job."""
            return await store.update_schedule(
                job_id,
                name=name,
                prompt=prompt,
                schedule_type=schedule_type,
                schedule_expr=schedule_expr,
                timezone=timezone,
                enabled=enabled,
            )

        @tool
        async def schedule_delete(job_id: str) -> bool:
            """Delete a scheduled autonomous job."""
            return await store.delete_schedule(job_id)

        # ---- Notifications --------------------------------------------
        @tool
        async def notify_user(title: str, body: str, level: str = "info") -> dict[str, Any]:
            """Create an in-app notification and best-effort local desktop notification."""
            return await notifications.notify(title, body, level=level)

        @tool
        async def notification_list(unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
            """List persistent Trajecta notifications."""
            return await store.list_notifications(unread_only=unread_only, limit=limit)

        @tool
        async def notification_mark_read(notification_id: str) -> bool:
            """Mark a Trajecta notification as read."""
            return await store.mark_notification_read(notification_id)

        # ---- Free web/network -----------------------------------------
        @tool
        async def web_search(query: str, max_results: int = 8) -> list[dict[str, str]]:
            """Search the public web using configured self-hosted SearXNG or a free best-effort fallback."""
            return await network.web_search(query, max_results=max_results)

        @tool
        async def web_extract(url: str, max_chars: int = 40_000) -> dict[str, Any]:
            """Fetch a page and recover using alternative web sources."""
            result = await network.web_extract(url, max_chars=max_chars)
            if not result.get("ok", True):
                raise ToolException(json.dumps(result, ensure_ascii=False))
            return result

        # Convert handled ToolException into an error ToolMessage.
        # The agent can reason about it and keep executing.
        web_extract.handle_tool_error = True

        @tool
        async def rss_read(url: str, max_items: int = 20) -> dict[str, Any]:
            """Read an RSS/Atom feed without a paid API."""
            return await network.rss_read(url, max_items=max_items)

        @tool
        async def url_metadata(url: str) -> dict[str, Any]:
            """Inspect URL status, final URL, content type, size and title metadata."""
            return await network.url_metadata(url)

        @tool
        async def http_request(
            method: str,
            url: str,
            headers: dict[str, str] | None = None,
            query: dict[str, str] | None = None,
            json_body: Any | None = None,
            text_body: str | None = None,
        ) -> dict[str, Any]:
            """Make a general HTTP request. This can mutate remote systems and is approval-gated."""
            return await network.http_request(
                method=method,
                url=url,
                headers=headers,
                query=query,
                json_body=json_body,
                text_body=text_body,
            )

        @tool
        async def verified_http_mutation(
            method: str,
            url: str,
            readback_url: str,
            headers: dict[str, str] | None = None,
            json_body: Any | None = None,
            text_body: str | None = None,
            expected_json: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Mutate an HTTP API and independently GET the resulting resource before reporting success."""
            return await network.verified_http_mutation(
                method=method,
                url=url,
                headers=headers,
                json_body=json_body,
                text_body=text_body,
                readback_url=readback_url,
                expected_json=expected_json,
            )

        # ---- Rich documents / images / archives -----------------------
        @tool
        def document_metadata(path: str) -> dict[str, Any]:
            """Return safe metadata and SHA-256 for a /workspace or /uploads file."""
            return documents.metadata(path)

        @tool
        def document_read(path: str, max_chars: int = 60_000) -> dict[str, Any]:
            """Extract structured text/tables from PDF, DOCX, XLSX, PPTX, CSV, HTML, JSON, YAML, XML or text."""
            return documents.read(path, max_chars=max_chars)

        @tool
        def document_search(path: str, query: str, max_matches: int = 50) -> list[dict[str, Any]]:
            """Search extracted document contents for text matches."""
            return documents.search(path, query, max_matches=max_matches)

        @tool
        def ocr_image(path: str) -> dict[str, Any]:
            """Run free local Tesseract OCR on an image."""
            return documents.ocr_image(path)

        @tool
        def image_metadata(path: str) -> dict[str, Any]:
            """Inspect image dimensions, format, EXIF and frame count."""
            return documents.image_metadata(path)

        @tool
        def image_transform(
            path: str,
            output_path: str,
            width: int | None = None,
            height: int | None = None,
            crop: list[int] | None = None,
            format: str | None = None,
        ) -> dict[str, Any]:
            """Resize/crop/convert a local image into /workspace."""
            return documents.image_transform(path, output_path, width=width, height=height, crop=crop, format=format)

        @tool
        def archive_extract(path: str, destination: str) -> dict[str, Any]:
            """Safely extract ZIP/TAR archives into /workspace with path-traversal protection."""
            return documents.extract_archive(path, destination)

        @tool
        def archive_create(paths: list[str], output_path: str, format: str = "zip") -> dict[str, Any]:
            """Create ZIP/TAR/TAR.GZ archives from accessible local files."""
            return documents.create_archive(paths, output_path, format=format)

        # ---- Media -----------------------------------------------------
        @tool
        async def media_metadata(path: str) -> dict[str, Any]:
            """Inspect local audio/video streams and metadata using ffprobe."""
            return await media.metadata(path)

        @tool
        async def media_convert(path: str, output_path: str) -> dict[str, Any]:
            """Convert audio/video locally using ffmpeg."""
            return await media.convert(path, output_path)

        @tool
        async def video_frame(path: str, at_seconds: float = 0.0) -> dict[str, Any]:
            """Extract one video frame to /workspace; inspect it with read_file for multimodal vision."""
            return await media.video_frame(path, at_seconds=at_seconds)

        @tool
        async def local_speech_to_text(path: str, model_size: str = "small") -> dict[str, Any]:
            """Transcribe local audio for free with faster-whisper when installed."""
            return await media.transcribe(path, model_size=model_size)

        @tool
        def local_text_to_speech(text: str, output_path: str) -> dict[str, Any]:
            """Create local speech audio using the OS/offline pyttsx3 engine."""
            return media.text_to_speech(text, output_path)

        # ---- Host/system ----------------------------------------------
        @tool
        def system_info() -> dict[str, Any]:
            """Inspect local OS, Python, CPU, RAM, disk and available GPU information."""
            return system.system_info()

        @tool
        def which(binary: str) -> dict[str, Any]:
            """Check whether a local executable is installed and return its path."""
            return system.which(binary)

        @tool
        def port_check(host: str, port: int, timeout_seconds: float = 1.5) -> dict[str, Any]:
            """Check whether a TCP port is reachable."""
            return system.port_check(host, port, timeout_seconds)

        @tool
        def network_info() -> dict[str, Any]:
            """Inspect local hostname, IP addresses and network interfaces."""
            return system.network_info()

        @tool
        def clipboard_read() -> str:
            """Read the current desktop clipboard text."""
            return system.clipboard_read()

        @tool
        def clipboard_write(text: str) -> str:
            """Replace the current desktop clipboard text."""
            return system.clipboard_write(text)

        @tool
        def wake_on_lan(mac_address: str, broadcast: str = "255.255.255.255", port: int = 9) -> str:
            """Send a Wake-on-LAN magic packet to a local-network device."""
            return system.wake_on_lan(mac_address, broadcast=broadcast, port=port)

        # ---- Deterministic utilities ----------------------------------
        @tool
        def calculate(expression: str) -> dict[str, Any]:
            """Safely evaluate arithmetic and common math functions without an LLM."""
            return utility.calculate(expression)

        @tool
        def unit_convert(value: float, from_unit: str, to_unit: str) -> dict[str, Any]:
            """Convert common length, mass, byte-size, time and temperature units."""
            return utility.unit_convert(value, from_unit, to_unit)

        @tool
        def timezone_convert(timestamp: str, from_timezone: str, to_timezone: str) -> str:
            """Convert an ISO timestamp between IANA timezones."""
            return utility.timezone_convert(timestamp, from_timezone, to_timezone)

        @tool
        def current_time(timezone: str = "UTC") -> str:
            """Return current time in an IANA timezone."""
            return utility.now(timezone)

        @tool
        def date_add(timestamp: str, days: int = 0, hours: int = 0, minutes: int = 0) -> str:
            """Add a deterministic duration to an ISO timestamp."""
            return utility.date_add(timestamp, days=days, hours=hours, minutes=minutes)

        @tool
        def uuid_generate() -> str:
            """Generate a random UUID4."""
            return utility.uuid_generate()

        @tool
        def hash_text(text: str, algorithm: str = "sha256") -> str:
            """Hash text with a hashlib-supported algorithm."""
            return utility.hash_text(text, algorithm)

        @tool
        def base64_codec(value: str, operation: str = "encode") -> str:
            """Encode or decode UTF-8 text using Base64."""
            return utility.base64_codec(value, operation)

        @tool
        def url_codec(value: str, operation: str = "encode") -> str:
            """Percent-encode or decode URL text."""
            return utility.url_codec(value, operation)

        @tool
        def text_diff(before: str, after: str) -> str:
            """Return a unified diff between two text values."""
            return utility.text_diff(before, after)

        @tool
        def regex_find(pattern: str, text: str, flags: str = "") -> list[dict[str, Any]]:
            """Find regex matches with spans and capture groups."""
            return utility.regex_find(pattern, text, flags=flags)

        # ---- SQLite ----------------------------------------------------
        @tool
        def sqlite_query(database_path: str, sql: str, parameters: list[Any] | None = None, limit: int = 500) -> dict[str, Any]:
            """Run a read-only SQL query against a SQLite database in /workspace or /uploads."""
            return database.sqlite_query(database_path, sql, parameters, limit=limit)

        @tool
        def sqlite_schema(database_path: str) -> list[dict[str, Any]]:
            """Inspect tables, views and indexes in a local SQLite database."""
            return database.sqlite_schema(database_path)

        @tool
        def sqlite_execute(database_path: str, sql: str, parameters: list[Any] | None = None) -> dict[str, Any]:
            """Execute a mutating SQLite statement in /workspace. Approval-gated."""
            return database.sqlite_execute(database_path, sql, parameters)

        # ---- Host long-running processes ------------------------------
        @tool
        async def process_start(command: str, cwd: str = "/workspace/", env: dict[str, str] | None = None) -> dict[str, Any]:
            """Start an approved long-running HOST process inside /workspace. Prefer sandbox execute for ordinary commands."""
            return await processes.start(command, cwd=cwd, env=env)

        @tool
        async def process_list() -> list[dict[str, Any]]:
            """List host processes launched by Trajecta's process manager."""
            return await processes.list()

        @tool
        async def process_status(process_id: str) -> dict[str, Any]:
            """Inspect status of a Trajecta-managed host process."""
            return await processes.status(process_id)

        @tool
        async def process_output(process_id: str, stdout_chars: int = 20_000, stderr_chars: int = 20_000) -> dict[str, Any]:
            """Read bounded stdout/stderr from a Trajecta-managed host process."""
            return await processes.output(process_id, stdout_chars=stdout_chars, stderr_chars=stderr_chars)

        @tool
        async def process_input(process_id: str, text: str) -> dict[str, Any]:
            """Send stdin to a Trajecta-managed host process. Approval-gated."""
            return await processes.send_input(process_id, text)

        @tool
        async def process_stop(process_id: str, force: bool = False) -> dict[str, Any]:
            """Stop a Trajecta-managed host process. Approval-gated."""
            return await processes.stop(process_id, force=force)

        custom: list[BaseTool] = [
            ask_user, request_approval,
            memory_search, memory_read, memory_save, memory_forget,
            session_search, session_read,
            skill_list, skill_search, skill_view, skill_candidate_create, skill_candidate_list, skill_candidate_update,
            trajectory_search,
            task_create, task_list, task_update, task_delete,
            schedule_create, schedule_list, schedule_update, schedule_delete,
            notify_user, notification_list, notification_mark_read,
            web_search, web_extract, rss_read, url_metadata, http_request, verified_http_mutation,
            document_metadata, document_read, document_search, ocr_image, image_metadata, image_transform,
            archive_extract, archive_create,
            media_metadata, media_convert, video_frame, local_speech_to_text, local_text_to_speech,
            system_info, which, port_check, network_info, clipboard_read, clipboard_write, wake_on_lan,
            calculate, unit_convert, timezone_convert, current_time, date_add, uuid_generate,
            hash_text, base64_codec, url_codec, text_diff, regex_find,
            sqlite_query, sqlite_schema, sqlite_execute,
            process_start, process_list, process_status, process_output, process_input, process_stop,
        ]

        # ---- Browser ---------------------------------------------------
        if self.settings.tools.browser.enabled:
            @tool
            async def browser_navigate(url: str, wait_until: str = "domcontentloaded") -> dict[str, Any]:
                """Open a web page in Trajecta's local Playwright browser."""
                return await browser.navigate(url, wait_until=wait_until)

            @tool
            async def browser_snapshot(max_text_chars: int = 30_000) -> dict[str, Any]:
                """Read current browser page text plus stable interactive element ids."""
                return await browser.snapshot(max_text_chars=max_text_chars)

            @tool
            async def browser_click(element_id: str) -> dict[str, Any]:
                """Click an element id from browser_snapshot. Approval-gated."""
                return await browser.click(element_id)

            @tool
            async def browser_submit_and_verify(
                element_id: str,
                expected_url_contains: str | None = None,
                expected_text: str | None = None,
            ) -> dict[str, Any]:
                """Submit/click a browser form action and verify the resulting page state."""
                return await browser.submit_and_verify(
                    element_id,
                    expected_url_contains=expected_url_contains,
                    expected_text=expected_text,
                )

            @tool
            async def browser_type(element_id: str, text: str, submit: bool = False) -> dict[str, Any]:
                """Fill an input from browser_snapshot; optionally press Enter. Approval-gated."""
                return await browser.type(element_id, text, submit=submit)

            @tool
            async def browser_press(key: str) -> dict[str, Any]:
                """Send a keyboard key/chord to the browser. Approval-gated."""
                return await browser.press(key)

            @tool
            async def browser_scroll(x: int = 0, y: int = 700) -> dict[str, Any]:
                """Scroll the current browser page."""
                return await browser.scroll(x=x, y=y)

            @tool
            async def browser_back() -> dict[str, Any]:
                """Navigate browser history backward."""
                return await browser.back()

            @tool
            async def browser_forward() -> dict[str, Any]:
                """Navigate browser history forward."""
                return await browser.forward()

            @tool
            async def browser_tabs() -> list[dict[str, Any]]:
                """List open browser tabs."""
                return await browser.tabs()

            @tool
            async def browser_select_tab(index: int) -> dict[str, Any]:
                """Select an existing browser tab by index."""
                return await browser.select_tab(index)

            @tool
            async def browser_new_tab(url: str | None = None) -> dict[str, Any]:
                """Open a new browser tab, optionally at a URL."""
                return await browser.new_tab(url)

            @tool
            async def browser_close_tab(index: int | None = None) -> dict[str, Any]:
                """Close the current or selected browser tab."""
                return await browser.close_tab(index)

            @tool
            async def browser_screenshot(full_page: bool = False) -> dict[str, Any]:
                """Capture browser screenshot to /workspace; use read_file on returned path for vision."""
                return await browser.screenshot(full_page=full_page)

            @tool
            async def browser_download(element_id: str) -> dict[str, Any]:
                """Click a download element and save its file under /workspace. Approval-gated."""
                return await browser.download(element_id)

            @tool
            async def browser_upload(element_id: str, paths: list[str]) -> dict[str, Any]:
                """Upload local /workspace or /uploads files through a browser file input. Approval-gated."""
                return await browser.upload(element_id, paths)

            @tool
            async def browser_cookies() -> list[dict[str, Any]]:
                """List browser cookie metadata without exposing secret cookie values."""
                return await browser.cookies()

            custom.extend([
                browser_navigate, browser_snapshot, browser_click, browser_type, browser_press,
                browser_scroll, browser_back, browser_forward, browser_tabs, browser_select_tab,
                browser_new_tab, browser_close_tab, browser_screenshot, browser_download,
                browser_upload, browser_cookies, browser_submit_and_verify,
            ])

        # ---- Full desktop control (opt-in) ----------------------------
        if self.settings.tools.computer.enabled:
            @tool
            def computer_screenshot() -> dict[str, Any]:
                """Capture the desktop to /workspace; inspect returned image path with read_file."""
                return computer.screenshot()

            @tool
            def computer_screen_size() -> dict[str, int]:
                """Return desktop screen dimensions."""
                return computer.screen_size()

            @tool
            def computer_click(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
                """Click screen coordinates. Approval-gated."""
                return computer.click(x, y, button=button, clicks=clicks)

            @tool
            def computer_move(x: int, y: int, duration: float = 0.2) -> dict[str, Any]:
                """Move desktop pointer. Approval-gated."""
                return computer.move(x, y, duration=duration)

            @tool
            def computer_drag(x: int, y: int, duration: float = 0.5, button: str = "left") -> dict[str, Any]:
                """Drag desktop pointer. Approval-gated."""
                return computer.drag(x, y, duration=duration, button=button)

            @tool
            def computer_scroll(amount: int, x: int | None = None, y: int | None = None) -> dict[str, Any]:
                """Scroll desktop UI. Approval-gated."""
                return computer.scroll(amount, x=x, y=y)

            @tool
            def computer_type(text: str, interval: float = 0.01) -> dict[str, Any]:
                """Type into the focused desktop application. Approval-gated."""
                return computer.type(text, interval=interval)

            @tool
            def computer_key(key: str) -> dict[str, Any]:
                """Press a desktop keyboard key. Approval-gated."""
                return computer.key(key)

            @tool
            def computer_shortcut(keys: list[str]) -> dict[str, Any]:
                """Press a desktop keyboard shortcut. Approval-gated."""
                return computer.shortcut(keys)

            custom.extend([
                computer_screenshot, computer_screen_size, computer_click, computer_move,
                computer_drag, computer_scroll, computer_type, computer_key, computer_shortcut,
            ])

        self._tools = custom
        return list(custom)

    async def close(self) -> None:
        """Release handles this provider opened.

        Workspace-scoped clones own their process and browser handles, so they
        are closed first. The shared HTTP stack is only closed by the root
        provider — a clone's shutdown must not cut off conversations still
        using it.
        """
        for clone in list(self._root_provider._clones.values()):
            await clone._close_own()
        self._root_provider._clones.clear()

        await self._close_own()

        if self._root_provider is self:
            await self.network.close()

    async def _close_own(self) -> None:
        await self.processes.close()
        await self.browser.close()

    def catalog(self) -> list[dict[str, Any]]:
        builtins = [
            "ls", "read_file", "write_file", "edit_file", "delete", "glob", "grep", "task", "write_todos"
        ]
        if self.memory.sandbox is not None:
            builtins.append("execute")
        result = [{"name": name, "source": "deepagents", "enabled": True} for name in builtins]
        for item in self.get_tools():
            result.append({
                "name": item.name,
                "description": item.description,
                "source": "trajecta",
                "enabled": True,
            })
        return result
