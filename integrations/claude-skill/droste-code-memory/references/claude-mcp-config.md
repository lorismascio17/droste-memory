# Claude MCP Configuration Reference

Use these snippets when configuring Droste for a Claude-facing MCP client.

## Basic Server Command

```bash
droste mcp
```

## JSON MCP Configuration

```json
{
  "mcpServers": {
    "droste": {
      "command": "droste",
      "args": ["mcp"]
    }
  }
}
```

## JSON MCP Configuration With Isolated DB

```json
{
  "mcpServers": {
    "droste": {
      "command": "droste",
      "args": [
        "--db",
        "/absolute/path/to/droste_memory_db.json",
        "mcp"
      ]
    }
  }
}
```

Windows path example:

```json
{
  "mcpServers": {
    "droste": {
      "command": "droste",
      "args": [
        "--db",
        "C:/Users/you/AppData/Local/Droste/my-project/droste_memory_db.json",
        "mcp"
      ]
    }
  }
}
```

Restart the Claude client after editing MCP config. In the target repository,
call `droste_index_project` before `droste_get_context`.

