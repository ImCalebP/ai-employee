"""
Parallel Action Executor
- Execute multiple intents simultaneously using asyncio
- Coordinate dependencies between actions
- Handle errors gracefully with rollback capabilities
- Provide real-time status updates
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from pydantic import BaseModel

logging.getLogger(__name__).setLevel(logging.INFO)


class ActionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ActionResult(BaseModel):
    action: str
    status: ActionStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration: Optional[float] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ParallelExecutor:
    """Execute multiple actions in parallel with dependency management."""
    
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.results: Dict[str, ActionResult] = {}
        self.dependencies: Dict[str, List[str]] = {}
        self.action_handlers = self._register_handlers()
    
    def _register_handlers(self) -> Dict[str, callable]:
        """Register all available action handlers."""
        from services.intent_api.email_agent import process_email_request
        from services.intent_api.reply_agent import process_reply
        from services.intent_api.document_agent import process_document_request
        from services.intent_api.task_agent import process_task_request
        
        return {
            "send_email": self._wrap_sync_handler(process_email_request),
            "reply": self._wrap_reply_handler(process_reply),
            "generate_reply": self._wrap_reply_handler(process_reply),
            "send_reply": self._wrap_reply_handler(process_reply),
            "send_message": self._wrap_reply_handler(process_reply),
            "generate_document": self._wrap_document_handler(process_document_request, "generate_from_text"),
            "share_document": self._wrap_document_handler(process_document_request, "share_document"),
            "create_task": self._wrap_task_handler(process_task_request, "create"),
            "extract_tasks": self._wrap_task_handler(process_task_request, "extract_and_create"),
            "update_task": self._wrap_task_handler(process_task_request, "update"),
            "compile_conversation_summary": self._wrap_document_handler(process_document_request, "generate_from_text"),
            "proactive_analysis": self._handle_proactive_analysis,
            "context_enrichment": self._handle_context_enrichment,
        }
    
    def _wrap_sync_handler(self, handler):
        """Wrap synchronous handler for async execution."""
        async def async_wrapper(params: Dict[str, Any]) -> Dict[str, Any]:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, handler, self.chat_id)
        return async_wrapper
    
    def _wrap_reply_handler(self, handler):
        """Wrap reply handler with custom parameters."""
        async def async_wrapper(params: Dict[str, Any]) -> Dict[str, Any]:
            loop = asyncio.get_event_loop()
            text = params.get("text", "")
            custom_prompt = params.get("custom_prompt")
            await loop.run_in_executor(None, handler, self.chat_id, text, None, custom_prompt)
            return {"status": "replied"}
        return async_wrapper
    
    def _wrap_document_handler(self, handler, action_type):
        """Wrap document handler with action type."""
        async def async_wrapper(params: Dict[str, Any]) -> Dict[str, Any]:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, handler, self.chat_id, action_type, params)
        return async_wrapper
    
    def _wrap_task_handler(self, handler, action_type):
        """Wrap task handler with action type."""
        async def async_wrapper(params: Dict[str, Any]) -> Dict[str, Any]:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, handler, self.chat_id, action_type, params)
        return async_wrapper
    
    async def _handle_proactive_analysis(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle proactive analysis of messages."""
        from common.enhanced_memory import analyze_message_for_proactive_actions
        
        message = params.get("message", "")
        sender = params.get("sender", "Unknown")
        
        loop = asyncio.get_event_loop()
        analysis = await loop.run_in_executor(
            None, 
            analyze_message_for_proactive_actions, 
            message, 
            self.chat_id, 
            sender
        )
        
        return {"status": "analyzed", "analysis": analysis}
    
    async def _handle_context_enrichment(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Handle context enrichment with document intelligence."""
        from common.enhanced_memory import get_contextual_intelligence
        
        query = params.get("query", "")
        
        loop = asyncio.get_event_loop()
        context = await loop.run_in_executor(
            None,
            get_contextual_intelligence,
            query,
            self.chat_id
        )
        
        return {"status": "enriched", "context": context}
    
    async def execute_action(self, action_id: str, action: str, params: Dict[str, Any]) -> ActionResult:
        """Execute a single action."""
        
        result = ActionResult(
            action=action,
            status=ActionStatus.PENDING,
            started_at=datetime.utcnow()
        )
        
        try:
            # Check if handler exists
            if action not in self.action_handlers:
                result.status = ActionStatus.FAILED
                result.error = f"No handler for action: {action}"
                return result
            
            # Update status to running
            result.status = ActionStatus.RUNNING
            self.results[action_id] = result
            
            # Execute the action
            handler = self.action_handlers[action]
            action_result = await handler(params)
            
            # Update result
            result.status = ActionStatus.COMPLETED
            result.result = action_result
            result.completed_at = datetime.utcnow()
            result.duration = (result.completed_at - result.started_at).total_seconds()
            
            logging.info(f"✓ Action completed: {action} ({result.duration:.2f}s)")
            
        except Exception as e:
            result.status = ActionStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.utcnow()
            result.duration = (result.completed_at - result.started_at).total_seconds()
            
            logging.error(f"✗ Action failed: {action} - {e}")
        
        return result
    
    def add_dependency(self, action_id: str, depends_on: List[str]) -> None:
        """Add dependency relationship between actions."""
        self.dependencies[action_id] = depends_on
    
    async def wait_for_dependencies(self, action_id: str) -> bool:
        """Wait for all dependencies to complete."""
        
        if action_id not in self.dependencies:
            return True
        
        dependencies = self.dependencies[action_id]
        
        while True:
            all_completed = True
            
            for dep_id in dependencies:
                if dep_id not in self.results:
                    all_completed = False
                    break
                
                dep_result = self.results[dep_id]
                if dep_result.status not in [ActionStatus.COMPLETED, ActionStatus.FAILED, ActionStatus.SKIPPED]:
                    all_completed = False
                    break
                
                # If dependency failed, this action should be skipped
                if dep_result.status == ActionStatus.FAILED:
                    return False
            
            if all_completed:
                return True
            
            # Wait a bit before checking again
            await asyncio.sleep(0.1)
    
    async def execute_parallel(
        self, 
        actions: List[Dict[str, Any]], 
        max_concurrent: int = 5
    ) -> Dict[str, ActionResult]:
        """Execute multiple actions in parallel with dependency management."""
        
        # Create semaphore to limit concurrent executions
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def execute_with_dependencies(action_data: Dict[str, Any]) -> ActionResult:
            action_id = action_data.get("id", f"{action_data['action']}_{len(self.results)}")
            action = action_data["action"]
            params = action_data.get("params", {})
            
            async with semaphore:
                # Wait for dependencies
                can_execute = await self.wait_for_dependencies(action_id)
                
                if not can_execute:
                    result = ActionResult(
                        action=action,
                        status=ActionStatus.SKIPPED,
                        error="Dependency failed",
                        started_at=datetime.utcnow(),
                        completed_at=datetime.utcnow()
                    )
                    self.results[action_id] = result
                    return result
                
                # Execute the action
                result = await self.execute_action(action_id, action, params)
                self.results[action_id] = result
                return result
        
        # Create tasks for all actions
        tasks = []
        for action_data in actions:
            task = asyncio.create_task(execute_with_dependencies(action_data))
            tasks.append(task)
        
        # Wait for all tasks to complete
        await asyncio.gather(*tasks, return_exceptions=True)
        
        return self.results
    
    def get_execution_summary(self) -> Dict[str, Any]:
        """Get summary of execution results."""
        
        total_actions = len(self.results)
        completed = sum(1 for r in self.results.values() if r.status == ActionStatus.COMPLETED)
        failed = sum(1 for r in self.results.values() if r.status == ActionStatus.FAILED)
        skipped = sum(1 for r in self.results.values() if r.status == ActionStatus.SKIPPED)
        
        total_duration = sum(
            r.duration for r in self.results.values() 
            if r.duration is not None
        )
        
        return {
            "total_actions": total_actions,
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "success_rate": completed / total_actions if total_actions > 0 else 0,
            "total_duration": total_duration,
            "results": {k: v.dict() for k, v in self.results.items()}
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def execute_actions_parallel(
    chat_id: str,
    actions: List[Dict[str, Any]],
    max_concurrent: int = 5
) -> Dict[str, Any]:
    """Execute multiple actions in parallel."""
    
    executor = ParallelExecutor(chat_id)
    
    # Add any dependencies specified in actions
    for action in actions:
        if "depends_on" in action:
            executor.add_dependency(action.get("id", action["action"]), action["depends_on"])
    
    # Execute all actions
    results = await executor.execute_parallel(actions, max_concurrent)
    
    # Return summary
    return executor.get_execution_summary()


def create_action(
    action: str,
    params: Dict[str, Any],
    action_id: Optional[str] = None,
    depends_on: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Helper to create action definition."""
    
    action_def = {
        "action": action,
        "params": params
    }
    
    if action_id:
        action_def["id"] = action_id
    
    if depends_on:
        action_def["depends_on"] = depends_on
    
    return action_def
