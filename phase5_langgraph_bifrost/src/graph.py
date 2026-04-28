from typing import TypedDict, Annotated, Sequence, Literal
import operator
import os
import requests
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI

class AgentState(TypedDict):
    """The state graph for the multi-agent travel orchestrator."""
    messages: Annotated[Sequence[BaseMessage], operator.add]
    next_agent: str
    itinerary_fragments: list[str]

def _call_external_agent(agent_name: str, url_env_var: str, default_url: str, state: AgentState) -> dict:
    """Helper to dispatch graph state to external distributed microservices."""
    host = os.environ.get(url_env_var, default_url)
    try:
        # Convert LangChain messages to primitive dicts for HTTP serialization
        serializable_state = {
            "messages": [{"role": m.type, "content": m.content} for m in state["messages"]],
            "itinerary_fragments": state.get("itinerary_fragments", [])
        }
        
        response = requests.post(f"{host}/reason", json={"state": serializable_state}, timeout=60)
        response.raise_for_status()
        result = response.json()
        
        return {
            "itinerary_fragments": [result.get("summary", f"{agent_name} executed successfully.")]
        }
    except Exception as e:
        return {
            "itinerary_fragments": [f"[{agent_name} ERROR]: {str(e)}"]
        }

def travel_planner_node(state: AgentState):
    """Initial planner analyzing intent and conditionally routing via BiFrost LLM."""
    bifrost_url = os.environ.get("BIFROST_GATEWAY_URL", "http://bifrost-gateway:8080")
    
    # Connect to the BiFrost AI Gateway acting as an OpenAI compatible endpoint
    llm = ChatOpenAI(
        model="llama3.2:3b",
        openai_api_key="bifrost-passthrough",
        openai_api_base=f"{bifrost_url}/v1",
        max_tokens=20
    )
    
    system_prompt = (
        "You are a routing supervisor for a travel agency. Based on the itinerary fragments so far, "
        "decide which specialized agent must act next. The EXACT output choices are: "
        "flight_agent, hotel_agent, activity_agent, weather_agent, FINISH\n\n"
        f"Fragments gathered: {state.get('itinerary_fragments', [])}\n\n"
        "If you have successfully secured flights, hotels, and activities based on the prompt, return EXACTLY the string: FINISH"
    )
    
    prompt_messages = [SystemMessage(content=system_prompt)] + list(state["messages"])
    
    try:
        response = llm.invoke(prompt_messages)
        content = response.content.strip().replace("\"", "")
        
        # Robust parsing mechanism for the LLM output
        choices = ["flight_agent", "hotel_agent", "activity_agent", "weather_agent", "FINISH"]
        next_node = "FINISH"
        for choice in choices:
            if choice in content:
                next_node = choice
                break
                
        return {"next_agent": next_node}
    except Exception as e:
        # Fallback in case BiFrost LLM is unavailable
        return {"next_agent": "FINISH"}

def flight_agent_node(state: AgentState):
    """Flight agent reasoning via external HTTP call."""
    return _call_external_agent("FlightAgent", "FLIGHT_AGENT_URL", "http://flight-agent:8000", state)

def hotel_agent_node(state: AgentState):
    """Hotel agent reasoning via external HTTP call."""
    return _call_external_agent("HotelAgent", "HOTEL_AGENT_URL", "http://hotel-agent:8000", state)

def activity_agent_node(state: AgentState):
    """Activity agent reasoning via external HTTP call."""
    return _call_external_agent("ActivityAgent", "ACTIVITY_AGENT_URL", "http://activity-agent:8000", state)

def weather_agent_node(state: AgentState):
    """Weather agent reasoning via external HTTP call."""
    return _call_external_agent("WeatherAgent", "WEATHER_AGENT_URL", "http://weather-agent:8000", state)

def _route_next(state: AgentState) -> str:
    """Read the intent parsed from the LLM."""
    return state.get("next_agent", "FINISH")

def create_graph():
    from langgraph.graph import StateGraph, END
    
    workflow = StateGraph(AgentState)
    workflow.add_node("planner", travel_planner_node)
    workflow.add_node("flight_agent", flight_agent_node)
    workflow.add_node("hotel_agent", hotel_agent_node)
    workflow.add_node("activity_agent", activity_agent_node)
    workflow.add_node("weather_agent", weather_agent_node)
    
    workflow.set_entry_point("planner")
    
    # Conditional Edges: The Planner LLM dynamically routes entirely based on user state
    workflow.add_conditional_edges("planner", _route_next, {
        "flight_agent": "flight_agent",
        "hotel_agent": "hotel_agent",
        "activity_agent": "activity_agent",
        "weather_agent": "weather_agent",
        "FINISH": END
    })
    
    # Sub-agents always return control to the supervisor
    workflow.add_edge("flight_agent", "planner")
    workflow.add_edge("hotel_agent", "planner")
    workflow.add_edge("activity_agent", "planner")
    workflow.add_edge("weather_agent", "planner")
    
    return workflow.compile()
