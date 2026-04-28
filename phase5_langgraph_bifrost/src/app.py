from fastapi import FastAPI
from pydantic import BaseModel
import os
import uvicorn
from contextlib import asynccontextmanager

from .graph import create_graph

# OpenTelemetry Implementation
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.langchain import LangchainInstrumentor

app = FastAPI(title="LangGraph Orchestrator")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize OTEL when the app starts
    LangchainInstrumentor().instrument()
    yield
    # Cleanup on shutdown
    LangchainInstrumentor().uninstrument()

app = FastAPI(lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)

workflow_graph = create_graph()

class PlanRequest(BaseModel):
    message: str

@app.post("/api/plan")
async def plan_trip(req: PlanRequest):
    """Execute the multi-agent workflow natively."""
    from langchain_core.messages import HumanMessage
    
    initial_state = {
        "messages": [HumanMessage(content=req.message)],
        "next_agent": "planner",
        "itinerary_fragments": []
    }
    
    # Executes the full loop inside LangGraph up to 25 limits
    final_state = workflow_graph.invoke(initial_state, {"recursion_limit": 25})
    
    return final_state

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
