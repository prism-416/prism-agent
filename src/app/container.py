from __future__ import annotations

from dataclasses import dataclass

from application.action_event_handler import ActionEventHandler
from application.context_provider import ContextProvider
from application.domain_event_handler import DomainEventHandler
from application.event_router import EventRouter
from application.executor import Executor
from application.planner import Planner
from application.recursion_runner import RecursionRunner
from application.trigger_policy import TriggerPolicy
from application.validator import Validator
from infrastructure.config.settings import Settings
from infrastructure.llm.gemini_model_provider import GeminiModelProvider
from infrastructure.llm.pydantic_ai_agent_factory import PydanticAIAgentFactory
from infrastructure.prism_api.client import PrismApiClient
from infrastructure.queue.base import Queue
from infrastructure.queue.memory_queue import MemoryQueue
from infrastructure.queue.oci_queue import OCIQueue
from infrastructure.registries.prompt_registry import PromptRegistry
from infrastructure.registries.skill_registry import SkillRegistry
from infrastructure.registries.tool_registry import ToolRegistry
from infrastructure.registries.workflow_registry import WorkflowRegistry
from infrastructure.state.base import StateStore
from infrastructure.state.memory_state_store import MemoryStateStore
from infrastructure.state.object_storage_state_store import ObjectStorageStateStore


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
    domain_event_handler: DomainEventHandler
    action_event_handler: ActionEventHandler
    recursion_runner: RecursionRunner


def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or Settings.from_env()
    queue = _build_queue(settings)
    state_store = _build_state_store(settings)
    prism_client = PrismApiClient(settings.prism_api_base_url, settings.prism_api_token)
    prompt_registry = PromptRegistry()
    tool_registry = ToolRegistry.from_prompt_registry(prompt_registry, prism_client=prism_client)
    skill_registry = SkillRegistry.from_prompt_registry(prompt_registry)
    workflow_registry = WorkflowRegistry.from_prompt_registry(prompt_registry)
    model_provider = GeminiModelProvider(settings)
    agent_factory = PydanticAIAgentFactory(
        model_provider=model_provider,
        live_llm_enabled=settings.app_env == "prod" and bool(settings.gemini_api_key),
    )
    planner = Planner(prompt_registry, skill_registry, tool_registry, agent_factory)
    executor = Executor(tool_registry)
    validator = Validator(prism_client)
    router = EventRouter(TriggerPolicy(), workflow_registry)
    context_provider = ContextProvider(prism_client, prompt_registry)
    domain_event_handler = DomainEventHandler(router, context_provider, planner, state_store, queue)
    action_event_handler = ActionEventHandler(executor, validator, state_store, queue)
    recursion_runner = RecursionRunner(
        queue=queue,
        state_store=state_store,
        domain_event_handler=domain_event_handler,
        action_event_handler=action_event_handler,
        max_recursion_depth=settings.max_recursion_depth,
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
        domain_event_handler=domain_event_handler,
        action_event_handler=action_event_handler,
        recursion_runner=recursion_runner,
    )


def _build_queue(settings: Settings) -> Queue:
    if settings.queue_backend == "memory":
        return MemoryQueue()
    return OCIQueue(settings.oci_queue_ocid, settings.oci_queue_messages_endpoint)


def _build_state_store(settings: Settings) -> StateStore:
    if settings.state_backend == "memory":
        return MemoryStateStore()
    return ObjectStorageStateStore(settings.oci_namespace, settings.oci_bucket_name)
