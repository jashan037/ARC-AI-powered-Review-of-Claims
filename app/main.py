from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest):
    # placeholder until wired to Azure Foundry agent
    return QueryResponse(answer=f"Received: {request.question}")
