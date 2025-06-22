"""
Shared Thread Pool Executor for the Intent API
Prevents resource exhaustion by limiting concurrent threads
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os

# Create a shared thread pool with limited workers
# Default to 10 workers, but allow configuration via environment variable
MAX_WORKERS = int(os.getenv("INTENT_API_MAX_WORKERS", "10"))

# Create the shared executor
shared_executor = ThreadPoolExecutor(
    max_workers=MAX_WORKERS,
    thread_name_prefix="intent_api_worker"
)

def get_shared_executor():
    """Get the shared thread pool executor."""
    return shared_executor

async def run_in_shared_executor(func, *args, **kwargs):
    """Run a function in the shared executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(shared_executor, func, *args, **kwargs)

# Cleanup function to be called on shutdown
def cleanup_executor():
    """Shutdown the shared executor gracefully."""
    shared_executor.shutdown(wait=True)
