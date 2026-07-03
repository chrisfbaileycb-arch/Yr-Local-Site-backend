"""Unit tests for agents, schemas, and pipelines using unittest.IsolatedAsyncioTestCase."""
import sys
from unittest.mock import AsyncMock, MagicMock

# --- Mock google.antigravity BEFORE importing agents ---
class MockLocalAgentConfig:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class MockAgent:
    def __init__(self, config=None):
        self.config = config

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def chat(self, prompt: str):
        response = MagicMock()
        if self.config and getattr(self.config, "response_schema", None):
            schema_name = self.config.response_schema.__name__
            if schema_name == "SiteGeneratorSchema":
                response.text = AsyncMock(return_value='{"name": "Test Business", "brandColor": "oklch(0.65 0.18 45)"}')
                response.structured_output = AsyncMock(return_value={
                    "name": "Test Business",
                    "slug": "test-business",
                    "seoTitle": "Test Title",
                    "seoDescription": "Test Desc",
                    "brandColor": "oklch(0.65 0.18 45)",
                    "sections": [{"kind": "hero", "headline": "Welcome"}]
                })
            elif schema_name == "AuditSchema":
                response.text = AsyncMock(return_value='{"score": 90}')
                response.structured_output = AsyncMock(return_value={
                    "score": 90,
                    "findings": [{"severity": "info", "category": "seo", "title": "Good title", "description": "No issues"}]
                })
            elif schema_name == "LeadProcessorSchema":
                response.text = AsyncMock(return_value='{"summary": "A lead request", "priority": 4}')
                response.structured_output = AsyncMock(return_value={
                    "summary": "A lead request",
                    "priority": 4
                })
            else:
                response.text = AsyncMock(return_value="mocked schema response")
                response.structured_output = AsyncMock(return_value={"mocked": True})
        else:
            response.text = AsyncMock(return_value=f"Response to: {prompt}")
            response.structured_output = AsyncMock(return_value=None)
        return response

mock_antigravity = MagicMock()
mock_antigravity.Agent = MockAgent
mock_antigravity.LocalAgentConfig = MockLocalAgentConfig
sys.modules["google.antigravity"] = mock_antigravity

# --- Now import agents ---
import unittest
from agents.site_generator import (
    create_site_generator_agent,
    LlmAgent,
    SiteGeneratorSchema,
    SiteSectionSchema
)
from agents.site_auditor import create_site_auditor_agent, AuditSchema, AuditFindingSchema
from agents.lead_processor import create_lead_processor_agent, LeadProcessorSchema
from agents.orchestrator import (
    create_generation_pipeline,
    create_audit_pipeline,
    SequentialAgent,
    SequentialAgentResponse
)

class TestAgentsAndPipelines(unittest.IsolatedAsyncioTestCase):
    async def test_llm_agent_wrapper_context_manager(self):
        """Verify LlmAgent context manager and chat behavior."""
        agent = LlmAgent(system_instructions="Test", response_schema=None)
        async with agent as active_agent:
            self.assertIs(active_agent, agent)
            response = await active_agent.chat("Hello")
            text = await response.text()
            self.assertEqual(text, "Response to: Hello")
            self.assertIsNone(await response.structured_output())

    async def test_llm_agent_outside_context_raises_runtime_error(self):
        """Verify LlmAgent raises error if chat is called outside of async with block."""
        agent = LlmAgent(system_instructions="Test", response_schema=None)
        with self.assertRaises(RuntimeError):
            await agent.chat("Hello")

    async def test_site_generator_agent_schema_and_output(self):
        """Verify Site Generator Agent configuration and structured output."""
        agent = create_site_generator_agent()
        self.assertEqual(agent.config.response_schema, SiteGeneratorSchema)
        async with agent as active_agent:
            response = await active_agent.chat("Build site for coffee shop")
            data = await response.structured_output()
            self.assertEqual(data["name"], "Test Business")
            self.assertEqual(data["brandColor"], "oklch(0.65 0.18 45)")
            self.assertEqual(len(data["sections"]), 1)
            self.assertEqual(data["sections"][0]["kind"], "hero")

    async def test_site_auditor_agent_schema_and_output(self):
        """Verify Site Auditor Agent configuration and structured output."""
        agent = create_site_auditor_agent()
        self.assertEqual(agent.config.response_schema, AuditSchema)
        async with agent as active_agent:
            response = await active_agent.chat("<html>...</html>")
            data = await response.structured_output()
            self.assertEqual(data["score"], 90)
            self.assertEqual(data["findings"][0]["severity"], "info")

    async def test_lead_processor_agent_schema_and_output(self):
        """Verify Lead Processor Agent configuration and structured output."""
        agent = create_lead_processor_agent()
        self.assertEqual(agent.config.response_schema, LeadProcessorSchema)
        async with agent as active_agent:
            response = await active_agent.chat("Budget: $5000")
            data = await response.structured_output()
            self.assertEqual(data["summary"], "A lead request")
            self.assertEqual(data["priority"], 4)

    async def test_sequential_agent_response_wrapper(self):
        """Verify SequentialAgentResponse correctly wraps response."""
        mock_last_response = MagicMock()
        
        response = SequentialAgentResponse(
            text="final output",
            last_response=mock_last_response,
            structured_output={"score": 100}
        )
        self.assertEqual(await response.text(), "final output")
        self.assertEqual(await response.structured_output(), {"score": 100})

    async def test_generation_pipeline(self):
        """Verify the creation and execution of the generation pipeline."""
        pipeline = create_generation_pipeline()
        self.assertIsInstance(pipeline, SequentialAgent)
        self.assertEqual(pipeline.name, "generation_pipeline")
        self.assertEqual(len(pipeline.sub_agents), 1)
        
        # Test chat through pipeline
        response = await pipeline.chat("Generate standard store")
        self.assertEqual(await response.text(), '{"name": "Test Business", "brandColor": "oklch(0.65 0.18 45)"}')
        data = await response.structured_output()
        self.assertEqual(data["name"], "Test Business")

    async def test_audit_pipeline(self):
        """Verify the creation and execution of the audit pipeline."""
        pipeline = create_audit_pipeline()
        self.assertIsInstance(pipeline, SequentialAgent)
        self.assertEqual(pipeline.name, "audit_pipeline")
        self.assertEqual(len(pipeline.sub_agents), 1)
        
        # Test chat through pipeline
        response = await pipeline.chat("Audit this site")
        self.assertEqual(await response.text(), '{"score": 90}')
        data = await response.structured_output()
        self.assertEqual(data["score"], 90)

    async def test_custom_sequential_agent_data_flow(self):
        """Verify sequential agent passes outputs to inputs sequentially."""
        # Create mock sub-agents
        agent1 = MagicMock(spec=LlmAgent)
        agent2 = MagicMock(spec=LlmAgent)
        
        # Configure context manager mocks
        agent1.__aenter__ = AsyncMock(return_value=agent1)
        agent1.__aexit__ = AsyncMock(return_value=None)
        agent2.__aenter__ = AsyncMock(return_value=agent2)
        agent2.__aexit__ = AsyncMock(return_value=None)
        
        # Configure chat mocks
        res1 = MagicMock()
        res1.text = AsyncMock(return_value="Intermediate output")
        res1.structured_output = AsyncMock(return_value={"intermediate": True})
        agent1.chat = AsyncMock(return_value=res1)
        
        res2 = MagicMock()
        res2.text = AsyncMock(return_value="Final output")
        res2.structured_output = AsyncMock(return_value={"final": True})
        agent2.chat = AsyncMock(return_value=res2)
        
        pipeline = SequentialAgent(name="custom_flow", sub_agents=[agent1, agent2])
        response = await pipeline.chat("Initial prompt")
        
        # Assertions
        agent1.chat.assert_called_once_with("Initial prompt")
        agent2.chat.assert_called_once_with("Intermediate output")
        self.assertEqual(await response.text(), "Final output")
        self.assertEqual(await response.structured_output(), {"final": True})

    async def test_llm_agent_concurrency_safety(self):
        """Verify that concurrent calls to the same LlmAgent do not overwrite each other's agent session."""
        import asyncio
        agent = LlmAgent(system_instructions="Test Concurrency", response_schema=None)
        
        results = []
        
        async def run_task(task_id: int, delay: float):
            async with agent as active_agent:
                # Retrieve the underlying agent from ContextVar to verify it exists and is distinct
                inner_agent1 = agent._agent_var.get()
                await asyncio.sleep(delay)
                # Retrieve again after sleep to ensure another task starting didn't overwrite it
                inner_agent2 = agent._agent_var.get()
                self.assertIs(inner_agent1, inner_agent2)
                
                response = await active_agent.chat(f"Prompt {task_id}")
                text = await response.text()
                results.append((task_id, inner_agent1, text))
                
        # Run two tasks concurrently
        await asyncio.gather(
            run_task(1, 0.1),
            run_task(2, 0.05)
        )
        
        self.assertEqual(len(results), 2)
        # Sort results by task_id to assert consistently
        results.sort(key=lambda x: x[0])
        # Ensure they used distinct MockAgent instances
        self.assertIsNot(results[0][1], results[1][1])
        # Ensure responses are correct
        self.assertEqual(results[0][2], "Response to: Prompt 1")
        self.assertEqual(results[1][2], "Response to: Prompt 2")

    def test_pydantic_schema_constraints(self):
        """Verify Pydantic constraint validation rules."""
        from pydantic import ValidationError

        # 1. SiteGeneratorSchema constraints
        # Invalid brandColor format (not matching oklch pattern)
        with self.assertRaises(ValidationError) as ctx:
            SiteGeneratorSchema(
                name="Test",
                slug="test",
                seoTitle="Valid Title",
                seoDescription="Valid Description",
                brandColor="rgb(255, 0, 0)",  # Invalid
                sections=[]
            )
        self.assertIn("brandColor", str(ctx.exception))

        # Invalid seoTitle (longer than 60 characters)
        long_title = "a" * 61
        with self.assertRaises(ValidationError) as ctx:
            SiteGeneratorSchema(
                name="Test",
                slug="test",
                seoTitle=long_title,
                seoDescription="Valid Description",
                brandColor="oklch(0.65 0.18 45)",
                sections=[]
            )
        self.assertIn("seoTitle", str(ctx.exception))

        # Invalid seoDescription (longer than 155 characters)
        long_desc = "a" * 156
        with self.assertRaises(ValidationError) as ctx:
            SiteGeneratorSchema(
                name="Test",
                slug="test",
                seoTitle="Valid Title",
                seoDescription=long_desc,
                brandColor="oklch(0.65 0.18 45)",
                sections=[]
            )
        self.assertIn("seoDescription", str(ctx.exception))

        # 2. AuditSchema constraints (score out of bounds)
        with self.assertRaises(ValidationError) as ctx:
            AuditSchema(score=101, findings=[])
        self.assertIn("score", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            AuditSchema(score=-1, findings=[])
        self.assertIn("score", str(ctx.exception))

        # 3. LeadProcessorSchema constraints (priority out of bounds)
        with self.assertRaises(ValidationError) as ctx:
            LeadProcessorSchema(summary="Lead", priority=6)
        self.assertIn("priority", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            LeadProcessorSchema(summary="Lead", priority=0)
        self.assertIn("priority", str(ctx.exception))

        # 4. New Schema Upgrades validation checks
        # Invalid SiteSectionSchema kind literal
        with self.assertRaises(ValidationError) as ctx:
            SiteSectionSchema(
                kind="blog",
                headline="Welcome"
            )
        self.assertIn("kind", str(ctx.exception))

        # Invalid SiteGeneratorSchema slug pattern
        with self.assertRaises(ValidationError) as ctx:
            SiteGeneratorSchema(
                name="Test Business",
                slug="test_business",  # underscore is invalid
                seoTitle="Title",
                seoDescription="Desc",
                brandColor="oklch(0.65 0.18 45)",
                sections=[]
            )
        self.assertIn("slug", str(ctx.exception))

        with self.assertRaises(ValidationError) as ctx:
            SiteGeneratorSchema(
                name="Test Business",
                slug="test--business",  # consecutive dashes invalid
                seoTitle="Title",
                seoDescription="Desc",
                brandColor="oklch(0.65 0.18 45)",
                sections=[]
            )
        self.assertIn("slug", str(ctx.exception))

        # Invalid AuditFindingSchema severity literal
        with self.assertRaises(ValidationError) as ctx:
            AuditFindingSchema(
                severity="unknown_severity",
                category="seo",
                title="Title",
                description="Desc"
            )
        self.assertIn("severity", str(ctx.exception))

        # Invalid AuditFindingSchema category literal
        with self.assertRaises(ValidationError) as ctx:
            AuditFindingSchema(
                severity="critical",
                category="unknown_category",
                title="Title",
                description="Desc"
            )
        self.assertIn("category", str(ctx.exception))
