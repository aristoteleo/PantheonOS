#!/usr/bin/env python3
"""
Tests for reverse call functionality in NATS backend

Covers:
- Multiple callbacks in a single call
- Callback being invoked multiple times
- Error handling in callbacks
- Mixed regular parameters with callbacks
- Backward compatibility (no callbacks)
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["PANTHEON_REMOTE_BACKEND"] = "nats"
os.environ["NATS_SERVERS"] = "nats://localhost:4222"

from pantheon.remote import RemoteBackendFactory, RemoteConfig


@pytest_asyncio.fixture
async def nats_backend():
    """NATS backend fixture"""
    config = RemoteConfig.from_config()
    backend = RemoteBackendFactory.create_backend(config)

    try:
        yield backend
    finally:
        try:
            if hasattr(backend, "_nc") and backend._nc:
                await backend._nc.close()
        except Exception:
            pass
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multiple_callbacks(nats_backend):
    """Test invoking a service with multiple callbacks"""

    # Worker with multiple callback parameters
    async def process_with_multiple_callbacks(callback1, callback2, value):
        result1 = await callback1(value)
        result2 = await callback2(result1)
        return result2

    worker = nats_backend.create_worker("test_multiple_callbacks")
    worker.register(process_with_multiple_callbacks)

    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(1)

    try:
        service = await nats_backend.connect(worker.service_id)

        def cb1(x):
            return x * 2

        async def cb2(x):
            return x + 10

        result = await service.invoke("process_with_multiple_callbacks", {
            "callback1": cb1,
            "callback2": cb2,
            "value": 5
        })

        # (5 * 2) + 10 = 20
        assert result == 20

    finally:
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_callback_with_multiple_calls(nats_backend):
    """Test callback being called multiple times"""

    call_count = {"value": 0}

    async def aggregate_calls(callback, iterations):
        total = 0
        for i in range(iterations):
            result = await callback(i)
            total += result
        return total

    worker = nats_backend.create_worker("test_multiple_calls")
    worker.register(aggregate_calls)

    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(1)

    try:
        service = await nats_backend.connect(worker.service_id)

        def counter_callback(x):
            call_count["value"] += 1
            return x * 2

        result = await service.invoke("aggregate_calls", {
            "callback": counter_callback,
            "iterations": 5
        })

        # Sum of 0*2 + 1*2 + 2*2 + 3*2 + 4*2 = 0 + 2 + 4 + 6 + 8 = 20
        assert result == 20
        assert call_count["value"] == 5

    finally:
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_callback_error_handling(nats_backend):
    """Test error handling in callback"""

    async def call_with_error_check(callback, value):
        try:
            result = await callback(value)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    worker = nats_backend.create_worker("test_callback_error")
    worker.register(call_with_error_check)

    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(1)

    try:
        service = await nats_backend.connect(worker.service_id)

        def failing_callback(x):
            if x < 0:
                raise ValueError("Negative value not allowed")
            return x * 2

        # Test successful call
        result1 = await service.invoke("call_with_error_check", {
            "callback": failing_callback,
            "value": 5
        })
        assert result1["success"] is True
        assert result1["result"] == 10

        # Test error case
        result2 = await service.invoke("call_with_error_check", {
            "callback": failing_callback,
            "value": -5
        })
        assert result2["success"] is False
        assert "Negative value not allowed" in result2["error"]

    finally:
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_mixed_parameters(nats_backend):
    """Test mixing regular parameters with callbacks"""

    async def complex_operation(base, multiplier, formatter):
        computed = base * multiplier
        formatted = await formatter(computed)
        return formatted

    worker = nats_backend.create_worker("test_mixed_params")
    worker.register(complex_operation)

    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(1)

    try:
        service = await nats_backend.connect(worker.service_id)

        async def format_callback(value):
            return f"Result: {value}"

        result = await service.invoke("complex_operation", {
            "base": 10,
            "multiplier": 3,
            "formatter": format_callback
        })

        assert result == "Result: 30"

    finally:
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_no_callback_still_works(nats_backend):
    """Test that services without callbacks work normally"""

    async def simple_add(a, b):
        return a + b

    worker = nats_backend.create_worker("test_no_callback")
    worker.register(simple_add)

    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(1)

    try:
        service = await nats_backend.connect(worker.service_id)

        result = await service.invoke("simple_add", {
            "a": 10,
            "b": 20
        })

        assert result == 30

    finally:
        await worker.stop()
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
