from langgraph.graph import StateGraph, END
from openmedia.state import AgentState
from openmedia.nodes import generator_node, validator_node, safety_reviewer_node

def create_agent():
    workflow = StateGraph(AgentState)

    # Add our nodes
    workflow.add_node("planner", generator_node)
    workflow.add_node("checker", validator_node)
    workflow.add_node("reviewer", safety_reviewer_node)

    # Set the flow
    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "checker")
    workflow.add_edge("checker", "reviewer")

    # The Decision Point
    def router(state: AgentState):
        if state.is_valid:
            return END
        if state.iteration_count >= 3:
            return END # Stop if we are looping too much
        return "planner"

    workflow.add_conditional_edges("reviewer", router)
    
    return workflow.compile()

# Initialize the agent
media_agent = create_agent()