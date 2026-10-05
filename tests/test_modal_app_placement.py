"""Generic placement owns late creation and stdio readiness through shutdown."""
import asyncio
import pytest

from pantheon.apps.modal_placement import ModalAppPlacement
from pantheon.apps.agent_execution_runner import ExecutionRecoveryRequired, ToolReceiptJournal
from test_modal_app_owner import Modal, owner
from test_modal_app_transport import Sandbox, until


class StreamingModal(Modal):
    async def create(self, *args, **kwargs):
        container = await super().create(*args, **kwargs)
        wire = Sandbox()
        container.stdout, container.stderr, container.stdin = wire.stdout, wire.stderr, wire.stdin
        self.wire = wire
        return container


@pytest.mark.asyncio
async def test_stop_before_readiness_joins_pipe_and_refuses_calls(tmp_path):
    sdk = StreamingModal()
    placement = ModalAppPlacement(owner(tmp_path, sdk))
    observer = asyncio.create_task(placement.start())
    await until(lambda: placement.pipe is not None)
    assert not observer.done()
    receipt = await placement.close()
    with pytest.raises(asyncio.CancelledError):
        await observer
    assert receipt == {'backend_id': placement.backend_id, 'stopped': True}
    with pytest.raises(RuntimeError):
        await placement.invoke('effect', {})
    assert await placement.close() == receipt
    assert next(iter(sdk.containers.values())).stops == 1


@pytest.mark.asyncio
async def test_cancelled_owner_waits_for_late_creation_and_confirmation(tmp_path):
    sdk = StreamingModal()
    sdk.release.clear()
    placement = ModalAppPlacement(owner(tmp_path, sdk))
    observer = asyncio.create_task(placement.start())
    await sdk.entered.wait()
    observer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await observer
    close = asyncio.create_task(placement.close())
    await asyncio.sleep(0)
    close.cancel()
    await asyncio.sleep(0)
    assert not close.done()
    sdk.release.set()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert (await placement.close())['stopped']
    assert len(sdk.calls) == 1 and next(iter(sdk.containers.values())).code == 137


@pytest.mark.asyncio
async def test_failed_stop_retains_placement_owner(tmp_path):
    sdk = StreamingModal()
    lease = owner(tmp_path, sdk)
    placement = ModalAppPlacement(lease)
    observer = asyncio.create_task(placement.start())
    await until(lambda: placement.pipe is not None)
    sdk.wire.ready()
    assert await observer is placement
    sdk.confirm_stop = False
    with pytest.raises(ExecutionRecoveryRequired):
        await placement.close()
    with pytest.raises(BlockingIOError):
        ToolReceiptJournal(tmp_path / 'owner', 'modal-app-owner')
    # Explicit test reconciliation: production owner never infers a stopped process.
    sdk.confirm_stop = True
    await lease.recover_stop()
    await placement.pipe.disconnect()
