"""Adversarial and boundary tests for LLM Agents and Sequential Pipelines."""
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

# --- Define custom exceptions to mimic google.antigravity.types ---
class AntigravityValidationError(Exception):
    pass

class AntigravityConnectionError(Exception):
    pass

# --- Create and register mock google.antigravity module BEFORE importing agents ---
class MockLocalAgentConfig:
    def __init__(self, model=None, system_instructions=None, response_schema=None, **kwargs):
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
        self.system_instructions = system_instructions
        self.response_schema = response_schema
        for k, v in kwargs.items():
            setattr(self, k, v)

class MockAgent:
    def __init__(self, config=None):
        self.config = config
        self.entered = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.entered = False

    async def chat(self, prompt: str):
        if not self.entered:
            raise RuntimeError("Agent not entered")
        
        # Simulate validation error if requested in prompt
        if "trigger_validation_error" in prompt:
            raise AntigravityValidationError("Invalid input/output format detected")
        
        # Simulate connection error if requested in prompt
        if "trigger_connection_error" in prompt:
            raise AntigravityConnectionError("WebSocket connection lost")
            
        # Simulate general exception if requested in prompt
        if "trigger_general_error" in prompt:
            raise RuntimeError("Internal unexpected error")

        response = MagicMock()
        
        # Setup async text response
        if "trigger_malformed_json" in prompt:
            response.text = AsyncMock(return_value="invalid json string {:")
            response.structured_output = AsyncMock(return_value=None)
        else:
            response.text = AsyncMock(return_value='{"name": "Fallback Name"}')
            response.structured_output = AsyncMock(return_value={"name": "Fallback Name"})
            
        return response

mock_antigravity = MagicMock()
mock_antigravity.Agent = MockAgent
mock_antigravity.LocalAgentConfig = MockLocalAgentConfig

# Mock types submodule
mock_types = MagicMock()
mock_types.AntigravityValidationError = AntigravityValidationError
mock_types.AntigravityConnectionError = AntigravityConnectionError
mock_antigravity.types = mock_types

sys.modules["google.antigravity"] = mock_antigravity
sys.modules["google.antigravity.types"] = mock_types

# --- Now import the agents ---
from agents.site_generator import LlmAgent, create_site_generator_agent
from agents.site_auditor import create_site_auditor_agent
from agents.lead_processor import create_lead_processor_agent
from agents.orchestrator import SequentialAgent, SequentialAgentResponse

class TestAgentBoundaries(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Clear environment variables to isolate environment tests
        self.original_env = os.environ.get("GEMINI_MODEL")
        if "GEMINI_MODEL" in os.environ:
            del os.environ["GEMINI_MODEL"]

    def tearDown(self):
        if self.original_env is not None:
            os.environ["GEMINI_MODEL"] = self.original_env
        elif "GEMINI_MODEL" in os.environ:
            del os.environ["GEMINI_MODEL"]

    def test_environment_variable_fallback(self):
        """Verify LlmAgent falls back to default model when environment is not set."""
        agent = LlmAgent(system_instructions="Test Instruction")
        self.assertEqual(agent.config.model, "gemini-3.5-flash")

    def test_environment_variable_custom(self):
        """Verify LlmAgent uses GEMINI_MODEL when it is set in environment."""
        os.environ["GEMINI_MODEL"] = "gemini-ultra-custom"
        agent = LlmAgent(system_instructions="Test Instruction")
        self.assertEqual(agent.config.model, "gemini-ultra-custom")

    def test_explicit_model_override(self):
        """Verify explicitly provided model overrides environment variable."""
        os.environ["GEMINI_MODEL"] = "gemini-ultra-custom"
        agent = LlmAgent(model="gemini-specific", system_instructions="Test Instruction")
        self.assertEqual(agent.config.model, "gemini-specific")

    async def test_llm_agent_raises_outside_context(self):
        """Verify calling chat outside of context raises RuntimeError."""
        agent = LlmAgent(system_instructions="Test")
        with self.assertRaises(RuntimeError) as ctx:
            await agent.chat("Hello")
        self.assertIn("LlmAgent must be used inside an 'async with' block.", str(ctx.exception))

    async def test_antigravity_validation_error_propagation(self):
        """Verify AntigravityValidationError propagates correctly from the agent."""
        agent = LlmAgent(system_instructions="Test")
        async with agent as active_agent:
            with self.assertRaises(AntigravityValidationError):
                await active_agent.chat("trigger_validation_error")

    async def test_antigravity_connection_error_propagation(self):
        """Verify AntigravityConnectionError propagates correctly from the agent."""
        agent = LlmAgent(system_instructions="Test")
        async with agent as active_agent:
            with self.assertRaises(AntigravityConnectionError):
                await active_agent.chat("trigger_connection_error")

    async def test_malformed_json_response_handling(self):
        """Verify agent handles cases where model returns malformed JSON."""
        agent = LlmAgent(system_instructions="Test")
        async with agent as active_agent:
            response = await active_agent.chat("trigger_malformed_json")
            self.assertEqual(await response.text(), "invalid json string {:")
            self.assertIsNone(await response.structured_output())

    async def test_sequential_agent_empty_pipeline(self):
        """Verify SequentialAgent behaves correctly with an empty list of agents."""
        pipeline = SequentialAgent(name="empty_pipeline", sub_agents=[])
        response = await pipeline.chat("Start Prompt")
        self.assertEqual(await response.text(), "Start Prompt")
        self.assertIsNone(await response.structured_output())

    async def test_sequential_agent_exception_cleanup(self):
        """Verify SequentialAgent exits properly if a sub-agent raises an exception."""
        agent1 = LlmAgent(system_instructions="Test 1")
        agent2 = LlmAgent(system_instructions="Test 2")
        
        pipeline = SequentialAgent(name="error_pipeline", sub_agents=[agent1, agent2])
        
        with self.assertRaises(AntigravityValidationError):
            # The first agent will raise, and the second agent should not be entered/left hanging
            await pipeline.chat("trigger_validation_error")
            
        self.assertIsNone(agent1._agent_var.get())
        self.assertIsNone(agent2._agent_var.get())

    async def test_sequential_agent_response_wrapping(self):
        """Verify SequentialAgentResponse correctly wraps text and structure."""
        mock_last_response = MagicMock()
        
        response = SequentialAgentResponse(
            text="final_text",
            last_response=mock_last_response,
            structured_output={"parsed": True}
        )
        self.assertEqual(await response.text(), "final_text")
        self.assertEqual(await response.structured_output(), {"parsed": True})

    async def test_sequential_agent_response_wrapping_no_last_response(self):
        """Verify SequentialAgentResponse handles None last_response."""
        response = SequentialAgentResponse(text="final_text", last_response=None)
        self.assertEqual(await response.text(), "final_text")
        self.assertIsNone(await response.structured_output())
