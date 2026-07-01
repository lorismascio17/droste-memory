# MCP Configuration Reference

Use these snippets when configuring Droste as an MCP server for an agent client.

## Basic Command

```bash
droste mcp
```

## Codex TOML

```toml
[mcp_servers.droste]
command = "droste"
args = ["mcp"]
startup_timeout_sec = 120
```

## Codex TOML With Isolated DB

```toml
[mcp_servers.droste]
command = "droste"
args = ["--db", "/absolute/path/to/droste_memory_db.json", "mcp"]
startup_timeout_sec = 120
```

On Windows, use forward slashes in TOML paths:

```toml
args = ["--db", "C:/Users/you/AppData/Local/Droste/my-project/droste_memory_db.json", "mcp"]
```

## JSON Clients

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

## JSON Clients With Isolated DB

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

Restart the client after editing MCP config. In the target repo, call
`droste_index_project` before `droste_get_context`.

