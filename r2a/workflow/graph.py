from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from r2a.core.state import R2AState
from r2a.workflow.nodes import (
    engineer_node,
    final_node,
    human_approval_node,
    manager_node,
    paper_node,
    planner_node,
    prepare_next_iteration_node,
    reviewer_node,
)
from r2a.workflow.router import approval_router, route_after_engineer, route_after_paper, route_after_planner, route_after_reviewer


def build_workflow_graph():
    graph = StateGraph(R2AState)
    graph.add_node("paper_node", paper_node)
    graph.add_node("planner_node", planner_node)
    graph.add_node("human_approval_node", human_approval_node)
    graph.add_node("engineer_node", engineer_node)
    graph.add_node("manager_node", manager_node)
    graph.add_node("reviewer_node", reviewer_node)
    graph.add_node("prepare_next_iteration_node", prepare_next_iteration_node)
    graph.add_node("final_node", final_node)

    graph.add_edge(START, "paper_node")
    graph.add_conditional_edges(
        "paper_node",
        route_after_paper,
        {"planner": "planner_node", "final": "final_node"},
    )
    graph.add_conditional_edges(
        "planner_node",
        route_after_planner,
        {"approval": "human_approval_node", "final": "final_node"},
    )
    graph.add_conditional_edges(
        "human_approval_node",
        approval_router,
        {"engineer": "engineer_node", "final": "final_node"},
    )
    graph.add_conditional_edges(
        "engineer_node",
        route_after_engineer,
        {"manager": "manager_node", "final": "final_node"},
    )
    graph.add_edge("manager_node", "reviewer_node")
    graph.add_conditional_edges(
        "reviewer_node",
        route_after_reviewer,
        {"prepare_next_iteration": "prepare_next_iteration_node", "final": "final_node"},
    )
    graph.add_edge("prepare_next_iteration_node", "planner_node")
    graph.add_edge("final_node", END)
    return graph.compile()


def create_research_graph():
    return build_workflow_graph()
