from __future__ import annotations

from dataclasses import dataclass

from application.action_event_handler import ActionEventHandler
from application.agent_run_sync import AgentRunSync
from application.context_provider import ContextProvider
from application.domain_event_handler import DomainEventHandler
from application.event_router import EventRouter
from application.executor import Executor
from application.orchestrator import Orchestrator
from application.planner import Planner
from application.recursion_runner import RecursionRunner
from application.subagent_coordinator import SubAgentCoordinator
from application.subagent_runner import SubAgentRunner
from application.trigger_policy import TriggerPolicy
from application.validator import Validator
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_embeddings import GeminiQueryEmbedder
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.llm.pydantic_ai_agent_factory import PydanticAIAgentFactory
from infrastructure.object_storage.oci_agent_memory_store import ObjectStorageAgentMemoryStore
from infrastructure.object_storage.oci_payload_store import ObjectStoragePayloadStore
from infrastructure.observability.logging_config import configure_logging
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.queue.base import Queue
from infrastructure.queue.memory_queue import MemoryQueue
from infrastructure.queue.oci_queue import OCIQueue
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry
from infrastructure.state.base import StateStore
from infrastructure.state.logging_state_store import LoggingStateStore
from infrastructure.state.memory_state_store import MemoryStateStore
from infrastructure.state.prism_api_state_store import PrismApiStateStore


@dataclass
class AppContainer:
    settings: Settings
    queue: Queue
    state_store: StateStore
    prism_client: PrismApiClient
    prompt_registry: PromptRegistry
    tool_registry: ToolRegistry
    skill_registry: SkillRegistry
    workflow_registry: WorkflowRegistry
    planner: Planner
    executor: Executor
    validator: Validator
    router: EventRouter
    context_provider: ContextProvider
    orchestrator: Orchestrator
    subagent_runner: SubAgentRunner
    subagent_coordinator: SubAgentCoordinator
    agent_run_sync: AgentRunSync
    domain_event_handler: DomainEventHandler
    action_event_handler: ActionEventHandler
    recursion_runner: RecursionRunner


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or Settings.from_env()
    queue = _build_queue(settings)
    prism_client = PrismApiClient(settings.prism_api_base_url, settings.prism_api_token)
    state_store = _build_state_store(settings, prism_client)
    if settings.log_traces:
        state_store = LoggingStateStore(state_store, configure_logging(settings.log_level))
    prompt_registry = PromptRegistry()
    embedder = GeminiQueryEmbedder(settings)
    tool_registry = ToolRegistry.from_prompt_registry(
        prompt_registry, prism_client=prism_client, embedder=embedder
    )
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    workflow_registry = WorkflowRegistry.from_prompt_registry(prompt_registry)
    model_provider = GeminiModelProvider(settings)
    agent_factory = PydanticAIAgentFactory(model_provider=model_provider)
    planner = Planner(prompt_registry, skill_registry, tool_registry, agent_factory)
    executor = Executor(tool_registry)
    validator = Validator(prism_client)
    router = EventRouter(TriggerPolicy(), workflow_registry)
    diff_payload_store = (
        ObjectStoragePayloadStore(
            settings.object_storage_namespace,
            settings.object_storage_bucket_name,
        )
        if settings.object_storage_namespace and settings.object_storage_bucket_name
        else None
    )
    context_provider = ContextProvider(prism_client, prompt_registry, diff_payload_store)
    orchestrator = Orchestrator(skill_registry)
    subagent_runner = SubAgentRunner(planner)
    agent_run_sync = AgentRunSync(
        prism_client,
        enabled=settings.state_backend == "prism_api",
    )
    domain_event_handler = DomainEventHandler(
        router,
        context_provider,
        planner,
        state_store,
        queue,
        agent_run_sync,
        orchestrator,
        subagent_runner,
    )
    action_event_handler = ActionEventHandler(
        executor,
        validator,
        state_store,
        queue,
        agent_run_sync,
    )
    subagent_coordinator = SubAgentCoordinator(
        state_store,
        queue,
        subagent_runner,
        agent_run_sync,
        workflow_registry,
    )
    recursion_runner = RecursionRunner(
        queue=queue,
        state_store=state_store,
        domain_event_handler=domain_event_handler,
        action_event_handler=action_event_handler,
        max_recursion_depth=settings.max_recursion_depth,
        agent_run_sync=agent_run_sync,
        subagent_coordinator=subagent_coordinator,
    )
    return AppContainer(
        settings=settings,
        queue=queue,
        state_store=state_store,
        prism_client=prism_client,
        prompt_registry=prompt_registry,
        tool_registry=tool_registry,
        skill_registry=skill_registry,
        workflow_registry=workflow_registry,
        planner=planner,
        executor=executor,
        validator=validator,
        router=router,
        context_provider=context_provider,
        orchestrator=orchestrator,
        subagent_runner=subagent_runner,
        subagent_coordinator=subagent_coordinator,
        agent_run_sync=agent_run_sync,
        domain_event_handler=domain_event_handler,
        action_event_handler=action_event_handler,
        recursion_runner=recursion_runner,
    )


def _build_queue(settings: Settings) -> Queue:
    if settings.queue_backend == "memory":
        return MemoryQueue()
    return OCIQueue(settings.oci_queue_ocid, settings.oci_queue_messages_endpoint)


def _build_state_store(settings: Settings, prism_client: PrismApiClient) -> StateStore:
    if settings.state_backend == "memory":
        return MemoryStateStore()
    # The internal-token runtime can't write Prism agent-memories (bearer scope), so
    # when a bucket is configured, persist durable agent state to Object Storage using
    # the function's own OCI credentials. Falls back to the Prism transport otherwise.
    memory_store = None
    if settings.object_storage_namespace and settings.object_storage_bucket_name:
        memory_store = ObjectStorageAgentMemoryStore(
            settings.object_storage_namespace,
            settings.object_storage_bucket_name,
        )
    return PrismApiStateStore(
        prism_client,
        persist_agent_memories=settings.persist_agent_memories,
        memory_store=memory_store,
    )
