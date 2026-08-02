"""Project-context contract; Docker integration suite exercises these routes with real stores."""
import inspect

from app.controllers import agent_task_controller, chat_controller, knowledge_base_controller, research_controller


def test_project_context_is_wired_to_creation_endpoints():
    for endpoint in (
        knowledge_base_controller.create_knowledge_base,
        research_controller.start_research_stream,
        agent_task_controller.create_task,
        chat_controller.create_conversation,
        chat_controller.chat_stream,
    ):
        assert "project_id" in inspect.signature(endpoint).parameters
