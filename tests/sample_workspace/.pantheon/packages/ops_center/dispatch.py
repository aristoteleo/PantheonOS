from pantheonos.toolset import ToolSet, tool


class OpsCenterToolSet(ToolSet):
    """ToolSet facade that represents async notification dispatch."""

    @tool
    async def notify(self, payload: dict, context_variables: dict | None = None):
        """Return the payload along with selected context for auditing."""
        context = dict(context_variables or {})
        return {
            "type": "notification",
            "payload": payload,
            "context_id": context.get("execution_context_id"),
            "client_id": context.get("client_id"),
        }
