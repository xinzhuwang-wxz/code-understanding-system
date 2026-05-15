"""MCP Server — Tool registry + concrete tool implementations."""
from .tools import Tool, ToolRegistry, ToolError, AutoRegisteringToolMeta
from .tool_impls import (
    SearchByPatternTool,
    SearchSemanticTool,
    TraverseGraphTool,
    GetConventionsTool,
    GetContextTool,
    AskQuestionTool,
    AnalyzeImpactTool,
    SearchDocsTool,
    ReviewCodeTool,
    GenerateTourTool,
    GenerateQuestionsTool,
)

__all__ = [
    "Tool", "ToolRegistry", "ToolError", "AutoRegisteringToolMeta",
    "SearchByPatternTool", "SearchSemanticTool", "TraverseGraphTool",
    "GetConventionsTool", "GetContextTool", "AskQuestionTool",
    "AnalyzeImpactTool", "SearchDocsTool", "ReviewCodeTool",
    "GenerateTourTool", "GenerateQuestionsTool",
]
