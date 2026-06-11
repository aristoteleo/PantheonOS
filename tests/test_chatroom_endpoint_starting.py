import pytest

from pantheon.chatroom.room import ChatRoom


class StartingEndpoint:
    service_name = "pantheon-chatroom-endpoint"
    service_id = "endpoint-service-123"
    _setup_completed = False

    async def proxy_toolset(self, **kwargs):
        raise AssertionError("proxy_toolset should not be called before endpoint setup completes")


def _chatroom_with_endpoint(endpoint):
    chatroom = ChatRoom.__new__(ChatRoom)
    chatroom._endpoint_embed = True
    chatroom._endpoint = endpoint
    chatroom.endpoint_service_id = None
    chatroom._endpoint_service = None
    return chatroom


@pytest.mark.asyncio
async def test_get_endpoint_reports_embedded_endpoint_starting():
    chatroom = _chatroom_with_endpoint(StartingEndpoint())

    result = await ChatRoom.get_endpoint(chatroom)

    assert result == {
        "success": True,
        "service_name": "pantheon-chatroom-endpoint",
        "service_id": "endpoint-service-123",
        "ready": False,
        "status": "starting",
    }


@pytest.mark.asyncio
async def test_proxy_toolset_returns_endpoint_not_ready_for_embedded_endpoint():
    chatroom = _chatroom_with_endpoint(StartingEndpoint())

    result = await ChatRoom.proxy_toolset(
        chatroom,
        method_name="read_file",
        args={"path": "README.md"},
        toolset_name="file_manager",
    )

    assert result == {
        "success": False,
        "error": "endpoint_not_ready",
        "code": "endpoint_not_ready",
        "status": "starting",
        "ready": False,
    }


@pytest.mark.asyncio
async def test_get_toolsets_returns_endpoint_not_ready_for_embedded_endpoint():
    chatroom = _chatroom_with_endpoint(StartingEndpoint())

    result = await ChatRoom.get_toolsets(chatroom)

    assert result == {
        "success": False,
        "error": "endpoint_not_ready",
        "code": "endpoint_not_ready",
        "status": "starting",
        "ready": False,
    }
