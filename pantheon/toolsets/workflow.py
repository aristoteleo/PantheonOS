from pathlib import Path

from ..toolset import ToolSet, tool
from ..utils.template import load_template, parse_items


class WorkflowToolSet(ToolSet):
    """Toolset for providing the agentic workflow service.
    
    Args:
        name: The name of the toolset.
        workflow_path: The path to the workflow config file or folder.
        worker_params: The parameters for the worker.
        endpoint_service_id: The endpoint service ID.
        **kwargs: Additional keyword arguments.
    """
    def __init__(
        self, 
        name: str, 
        workflow_path: str | Path | None = None,
        worker_params: dict | None = None,
        endpoint_service_id: str | None = None,
        **kwargs
    ):
        # Pass standard ToolSet parameters to parent class
        super().__init__(name, worker_params, endpoint_service_id)
        
        # Use default workflow path if not provided
        if workflow_path is None:
            import os
            # Default to bio_workflows in examples directory
            workflow_path = os.path.join(
                os.path.dirname(__file__), 
                "..", "..", "examples", "bio_cli", "bio_workflows"
            )
        
        template = load_template(workflow_path)
        template_items = parse_items(template)
        self.template_items = {
            '.'.join(item.command): item for item in template_items
        }

    @tool
    async def use_workflow(self, name: str, parameters: str = "{}"):
        """Get the information of some specific workflow.
        
        Args:
            name: The name of the workflow.
            parameters: JSON string containing the arguments for the workflow (e.g., '{"key": "value"}').
        """
        import json
        if name not in self.template_items:
            available_workflows = list(self.template_items.keys())
            raise ValueError(f"Workflow {name} not found. Available workflows: {available_workflows}")
        item = self.template_items[name]
        if item.args is None:
            return item.content
        else:
            parsed_kwargs = json.loads(parameters) if parameters else {}
            return item.content.format(**parsed_kwargs)

    @tool
    async def list_workflows(self):
        """List all available workflows. """
        res = {}
        for key, item in self.template_items.items():
            res[key] = {
                "description": item.description,
                "args": list(item.args.keys()) if item.args else [],
            }
        return res


__all__ = ["WorkflowToolSet"]
