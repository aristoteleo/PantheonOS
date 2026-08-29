"""A retired toolset lingering in a volume's settings must not wedge the boot.

The real-world shape (seen on staging volumes): settings.json's
services.builtin still lists 'package' after the toolset was removed.
Before the fix, startup failed the service on every boot AND
Endpoint.services_ready — which requires ALL builtins running — looped
forever, so the endpoint's NATS worker never registered and the desktop,
files and terminal all died. Chat kept working, which made it worse to
diagnose.
"""

import pytest

from pantheon.endpoint.toolsets import ToolSetManager


@pytest.fixture
def manager(tmp_path):
    return ToolSetManager(
        config={"builtin_services": [], "service_modes": {"default": "local"}},
        id_hash="test",
        endpoint_path=tmp_path,
        log_dir=tmp_path / "logs",
    )


def test_available_and_retired_service_types(manager):
    assert manager._service_type_available("file_manager")
    assert manager._service_type_available("shell")
    # the 2026-08 cleanup batch — none of these ship any more
    for retired in ("package", "knowledge", "vector_rag", "scfm", "code",
                    "julia_interpreter", "r_interpreter", "database_api_query"):
        assert not manager._service_type_available(retired), retired


@pytest.mark.asyncio
async def test_retired_builtin_is_skipped_not_failed(manager, tmp_path):
    result = await manager.start_services(["package"])
    assert result["success"] is True
    assert result.get("started") in ([], None) or "package" not in result.get("started", [])
    assert "package" in manager._unavailable_services


@pytest.mark.asyncio
async def test_services_ready_ignores_unavailable_builtins(tmp_path):
    from pantheon.endpoint.core import Endpoint

    endpoint = Endpoint(
        config={"builtin_services": ["package"]},
        workspace_path=str(tmp_path),
    )
    await endpoint.toolset_manager.start_services(["package"])
    assert "package" in endpoint.toolset_manager._unavailable_services
    # the exact predicate the boot's Phase-2 loop waits on
    assert await endpoint.services_ready() is True
