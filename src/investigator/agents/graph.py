"""The LangGraph loop:  plan -> investigate -> reason -> critique -> (plan | report) -> END.

The graph only sequences the work; the routing decision after each critique is the pure
function team.next_step(), so termination never depends on the model. Shared data lives on the
blackboard and in RunContext, so the graph state is just the round number.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from investigator.agents import team


class GraphState(TypedDict):
    round: int


def build_graph(ctx):
    async def plan(state):
        round_number = state["round"] + 1
        await team.plan_round(ctx, round_number)
        return {"round": round_number}

    async def investigate(state):
        await team.investigate(ctx, state["round"])
        return {}

    async def reason(state):
        await team.reason(ctx, state["round"])
        return {}

    async def critique(state):
        await team.critique(ctx, state["round"])
        return {}

    # async on purpose: LangGraph runs a plain function in a worker thread, and the
    # blackboard's SQLite connection may only be used from the thread that opened it.
    async def route(state):
        route_name, status = team.next_step(state["round"], ctx.max_rounds,
                                            team.has_open_blocking(ctx), ctx.stop_reason,
                                            approved=ctx.approved)
        ctx.status = status
        return route_name

    async def report(state):
        await team.write_report(ctx)
        return {}

    graph = StateGraph(GraphState)
    graph.add_node("plan", plan)
    graph.add_node("investigate", investigate)
    graph.add_node("reason", reason)
    graph.add_node("critique", critique)
    graph.add_node("report", report)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "investigate")
    graph.add_edge("investigate", "reason")
    graph.add_edge("reason", "critique")
    graph.add_conditional_edges("critique", route, {"plan": "plan", "report": "report"})
    graph.add_edge("report", END)
    return graph.compile()


def recursion_limit(max_rounds):
    """Four node visits per round plus the report and some slack."""
    return max_rounds * 4 + 5
