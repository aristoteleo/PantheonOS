"""Check actual kernel startup and child execution before a native desktop gate.

No application fixtures or monkeypatches: this also detects container/emulation
problems that would otherwise be mistaken for a Fleet RPC startup failure.
"""
import asyncio
import platform
import time

from jupyter_client import AsyncKernelManager


async def main():
    start = time.monotonic()
    manager = AsyncKernelManager()
    await manager.start_kernel(cwd='/tmp')
    client = manager.client()
    client.start_channels()
    try:
        await client.wait_for_ready(timeout=30)
        messages = []
        result = await client.execute_interactive(
            "import subprocess, sys\n"
            "print(subprocess.check_output([sys.executable, '-c', "
            "\"print('KERNEL_CHILD_OK')\"]).decode().strip())",
            timeout=15, output_hook=messages.append,
        )
        assert result['content']['status'] == 'ok', result
        assert any('KERNEL_CHILD_OK' in msg['content'].get('text', '') for msg in messages), messages
        print(f'Kernel and child process ready on {platform.machine()} in {time.monotonic() - start:.2f}s', flush=True)
    finally:
        client.stop_channels()
        await manager.shutdown_kernel(now=True)


if __name__ == '__main__':
    asyncio.run(main())
