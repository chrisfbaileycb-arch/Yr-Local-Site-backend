"""Multi-agent orchestration pipelines using a sequential execution wrapper."""
from typing import List, Any
from .site_generator import create_site_generator_agent
from .site_auditor import create_site_auditor_agent

class SequentialAgentResponse:
    """Wraps the sequential agent pipeline response to match SDK standards."""
    def __init__(self, text: str, last_response: Any, structured_output: Any = None):
        self._text = text
        self._last_response = last_response
        self._structured_output = structured_output

    async def text(self) -> str:
        """Returns the final text result."""
        return self._text

    async def structured_output(self) -> Any:
        """Returns the structured output of the final agent if available."""
        return self._structured_output

class SequentialAgent:
    """Custom wrapper executing sub-agents sequentially within an async context."""
    def __init__(self, name: str, sub_agents: List[Any]):
        self.name = name
        self.sub_agents = sub_agents

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def chat(self, prompt: str) -> SequentialAgentResponse:
        """Executes each sub-agent sequentially, passing the output of one to the next."""
        current_input = prompt
        response = None
        structured_output_val = None
        for agent in self.sub_agents:
            async with agent as active_agent:
                response = await active_agent.chat(current_input)
                current_input = await response.text()
                if response and hasattr(response, "structured_output"):
                    structured_output_val = await response.structured_output()
                else:
                    structured_output_val = None
        return SequentialAgentResponse(current_input, response, structured_output=structured_output_val)

def create_generation_pipeline() -> SequentialAgent:
    """Pipeline executing site generation."""
    return SequentialAgent(
        name="generation_pipeline",
        sub_agents=[create_site_generator_agent()],
    )

def create_audit_pipeline() -> SequentialAgent:
    """Pipeline executing website quality audit."""
    return SequentialAgent(
        name="audit_pipeline",
        sub_agents=[create_site_auditor_agent()],
    )
