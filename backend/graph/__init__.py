"""
Graph module — re-exports the core Node/Edge/Graph types from the analyzer,
plus the new KuzuDB-backed KnowledgeGraph store for persistence.
"""

from analyzer.graph import Node, Edge, Graph, NODE_TYPES, EDGE_TYPES, NODE_COLORS, EDGE_COLORS
from .kuzu_store import KnowledgeGraph, KGNode, KGEdge

__all__ = [
    "Node", "Edge", "Graph",
    "NODE_TYPES", "EDGE_TYPES",
    "NODE_COLORS", "EDGE_COLORS",
    "KnowledgeGraph", "KGNode", "KGEdge",
]
