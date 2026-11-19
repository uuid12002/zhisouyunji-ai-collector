"""Console entry point: stdout is reserved for the MCP stdio protocol."""

from collector.agent_bootstrap import run_mcp

if __name__ == '__main__':
    run_mcp()
