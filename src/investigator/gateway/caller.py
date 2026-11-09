"""McpToolCaller: one tool call over an open MCP client, returning the ToolResult envelope."""

from investigator.mcp_server import envelope_from_result


class McpToolCaller:
    def __init__(self, client):
        self.client = client          # an open mcp.Client (stdio subprocess or in-process)

    async def call(self, tool, args):
        result = await self.client.call_tool(tool, args)
        if result.structured_content is None:
            text = ""
            if result.content:
                text = result.content[0].text
            raise RuntimeError("tool server sent no result envelope: " + text)
        return envelope_from_result(result)
