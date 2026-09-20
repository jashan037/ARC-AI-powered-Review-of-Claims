"""Create (or add a new version of) the tool-using agent in your Foundry project.

    python scripts/setup/create_agent.py

Function tools cannot be added in the Foundry portal, only through the SDK/REST, so this script does it.
Run it again whenever you change instructions.py or the tool schemas: it creates a new agent version.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FunctionTool, PromptAgentDefinition
from azure.identity import DefaultAzureCredential

from app.agent.instructions import SYSTEM_PROMPT
from app.config import settings
from app.tools.registry import SCHEMAS

project = AIProjectClient(endpoint=settings.project_endpoint, credential=DefaultAzureCredential())
tools = [FunctionTool(name=s["name"], description=s["description"], parameters=s["parameters"], strict=False) for s in SCHEMAS]
agent = project.agents.create_version(
    agent_name=settings.agent_name,
    definition=PromptAgentDefinition(model=settings.model_deployment, instructions=SYSTEM_PROMPT, tools=tools))
print(f"Agent ready: name={agent.name} version={agent.version} tools={[s['name'] for s in SCHEMAS]}")
