import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_handshake_and_tools(library, tmp_path):
    _, one, two = library

    async def exercise():
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "editorial_mcp",
                "--book",
                f"uno={one}",
                "--book",
                f"dos={two}",
                "--database",
                str(tmp_path / "transport.sqlite3"),
                "serve",
            ],
        )
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                info = await client.initialize()
                assert info.serverInfo.name == "editorial"
                tools = (await client.list_tools()).tools
                assert {tool.name for tool in tools} == {
                    "index_status",
                    "search",
                    "read_scene",
                    "chapter_context",
                    "entity_evidence",
                    "lint_chapter",
                }
                assert all(tool.annotations.readOnlyHint for tool in tools)
                for tool in tools:
                    if tool.name != "index_status":
                        assert "book" in tool.inputSchema["required"]
                status = await client.call_tool("index_status", {})
                assert status.structuredContent["fresh"]
                assert status.structuredContent["books"] == ["uno", "dos"]
                result = await client.call_tool("search", {"book": "uno", "query": "dos mil"})
                assert not result.isError
                hit = result.structuredContent["results"][0]
                read = await client.call_tool(
                    "read_scene",
                    {
                        "book": "uno",
                        "scene_id": hit["scene_id"],
                        "expected_hash": hit["source_hash"],
                    },
                )
                assert "dos mil" in read.structuredContent["text"]
                context = await client.call_tool("chapter_context", {"book": "uno", "chapter": "4"})
                assert context.structuredContent["open_issue_chunks"] == 2
                evidence = await client.call_tool(
                    "entity_evidence", {"book": "dos", "entity": "Martha"}
                )
                assert evidence.structuredContent["total_matching_chunks"] == 1
                lint = await client.call_tool("lint_chapter", {"book": "uno", "chapter": "4"})
                assert lint.structuredContent["total_findings"] == 0
                invalid = await client.call_tool("search", {"book": "missing", "query": "Martha"})
                assert invalid.isError
                # El servidor sigue vivo tras un error de entrada.
                assert not (await client.call_tool("index_status", {})).isError

    asyncio.run(exercise())
