"""Integration tests for task lifecycle flow using scoped SDK.

These tests exercise the task state machine against the real local server,
with full isolation via scope — each test sees only its own data.

Run with a local server on port 9787:
    cd submodules/server && npx wrangler dev --port 9787
"""

import uuid

import pytest


class TestTaskLifecycleFlow:
    """Test task state machine transitions against real server."""

    def test_full_happy_path(self, scoped_sdk):
        """incoming → claimed → provisional → done"""
        task_id = f"TEST-{uuid.uuid4().hex[:8]}"
        task = scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Test task",
            role="implement",
            branch="main",
        )
        assert task["queue"] == "incoming"

        # Claim
        claimed = scoped_sdk.tasks.claim(
            orchestrator_id="test-orch",
            agent_name="test-agent",
            role_filter="implement",
        )
        assert claimed is not None
        assert claimed["id"] == task_id

        # Submit
        submitted = scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=5)
        assert submitted["queue"] == "provisional"

        # Accept
        accepted = scoped_sdk.tasks.accept(task_id, accepted_by="test-gatekeeper")
        assert accepted["queue"] == "done"

    def test_reject_returns_to_incoming(self, scoped_sdk):
        """provisional → incoming on reject"""
        task_id = f"TEST-{uuid.uuid4().hex[:8]}"
        scoped_sdk.tasks.create(
            id=task_id,
            file_path=f".octopoid/tasks/{task_id}.md",
            title="Reject test",
            role="implement",
            branch="main",
        )
        scoped_sdk.tasks.claim(
            orchestrator_id="test-orch",
            agent_name="test-agent",
            role_filter="implement",
        )
        scoped_sdk.tasks.submit(task_id, commits_count=1, turns_used=3)

        rejected = scoped_sdk.tasks.reject(task_id, reason="Tests fail")
        assert rejected["queue"] == "incoming"

    def test_scope_isolation(self, scoped_sdk, test_server_url):
        """Scoped SDK only sees its own tasks."""
        from octopoid_sdk import OctopoidSDK

        other_sdk = OctopoidSDK(
            server_url=test_server_url,
            scope=f"other-{uuid.uuid4().hex[:8]}",
        )
        try:
            task_id = f"TEST-{uuid.uuid4().hex[:8]}"
            scoped_sdk.tasks.create(
                id=task_id,
                file_path=f".octopoid/tasks/{task_id}.md",
                title="Scoped task",
                role="implement",
                branch="main",
            )

            # Same scope sees it
            mine = scoped_sdk.tasks.list(queue="incoming")
            assert any(t["id"] == task_id for t in mine)

            # Different scope does not
            theirs = other_sdk.tasks.list(queue="incoming")
            assert not any(t["id"] == task_id for t in theirs)
        finally:
            other_sdk.close()
