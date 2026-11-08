from mcp.server.mcpserver.exceptions import ToolError


class ErroDeNegocio(ToolError):
    """Violacao de regra de sala/politica; o SDK a devolve como resultado com isError=true."""
