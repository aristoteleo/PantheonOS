import asyncio


class AsyncRInterpreter:
    """
    An asynchronous class to control a running R interpreter process.

    Commands are sent to R along with an injected command that prints a unique marker.
    The output is read asynchronously until the marker is detected. The waiting
    and reading logic has been separated into its own method (`read_until_marker`)
    so that you can call it directly if needed.
    """
    def __init__(self, process, marker):
        self.process = process
        self.marker = marker
        self._output_queue = asyncio.Queue()
        self._stop_reading = False
        self._reader_task = asyncio.create_task(self._read_stdout())

    @classmethod
    async def create(cls, r_executable="R", r_args=None, marker="__COMMAND_FINISHED__"):
        """
        Asynchronously create an instance of AsyncRInterpreter.

        Parameters:
            r_executable (str): The R executable to run (e.g., "R.exe" or "Rterm.exe").
            r_args (list, optional): Command-line arguments for R.
            marker (str): A unique string used to mark the end of a command.

        Returns:
            AsyncRInterpreter: An initialized instance.
        """
        if r_args is None:
            # Suppress startup messages and disable workspace saving/restoring.
            r_args = ["--quiet", "--no-save", "--no-restore"]

        # Do not pass an encoding here – Windows' Proactor event loop requires binary pipes.
        process = await asyncio.create_subprocess_exec(
            r_executable, *r_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT
        )
        instance = cls(process, marker)
        # Allow a brief pause for any startup output.
        await asyncio.sleep(0.5)
        return instance

    async def _read_stdout(self):
        """
        Asynchronously read lines from the R process's stdout and enqueue them.
        """
        while not self._stop_reading:
            line = await self.process.stdout.readline()
            if line:
                # Decode the output from bytes to a string.
                line = line.decode('utf-8', errors='replace')
                await self._output_queue.put(line)
            else:
                break

    async def read_until_marker(self, marker=None, timeout=10):
        """
        Wait for and read lines from the output queue until a line containing the marker is encountered.

        Parameters:
            marker (str, optional): The marker to wait for. If not provided, uses self.marker.
            timeout (int, optional): Maximum time in seconds to wait for the marker.

        Returns:
            str: The combined output read until the marker is encountered.
                 The marker line is omitted.
        """
        if marker is None:
            marker = self.marker

        output_lines = []
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        while True:
            remaining = timeout - (loop.time() - start_time)
            if remaining <= 0:
                output_lines.append("\n[Warning] Timeout waiting for marker.")
                break
            try:
                line = await asyncio.wait_for(self._output_queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                output_lines.append("\n[Warning] Timeout waiting for marker.")
                break
            # Discriminate the injected marker from the R prompt output.
            # If the marker is found and the line does not contain "> ", consider it the marker.
            if marker in line:
                if "> " not in line:
                    break
                else:
                    # If it's the prompt line containing the marker, ignore it.
                    continue
            # filter out the R prompt
            if line.startswith("> ") or line.startswith("+ "):
                continue
            output_lines.append(line)
        return "".join(output_lines)


    async def send_command(self, command, timeout=10):
        """
        Send a command to the R interpreter and wait until the unique marker is detected.

        The marker is injected via a `cat()` command and then filtered out from the output.

        Parameters:
            command (str): The R command to execute.
            timeout (int, optional): Maximum time in seconds to wait for the marker.

        Returns:
            str: The combined output of the command (excluding the injected marker).
        """
        if self.process.returncode is not None:
            raise Exception("R interpreter process has terminated.")

        # Flush any leftover output from previous commands.
        while not self._output_queue.empty():
            try:
                self._output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Append a command that prints the unique marker.
        #full_command = f"{command}\ncat('{self.marker}\\n')\n"
        full_command = f"tryCatch({{{command}}}, error = function(e) {{message('Error: ', e$message) }})\ncat('{self.marker}\\n')\n"
        self.process.stdin.write(full_command.encode('utf-8'))
        await self.process.stdin.drain()

        # Use the separated method to wait and read until the marker.
        return await self.read_until_marker(timeout=timeout)

    async def close(self):
        """
        Gracefully close the R interpreter.
        """
        if self.process.returncode is None:
            # Send R's quit command.
            self.process.stdin.write(b"q()\n")
            await self.process.stdin.drain()
            # R may prompt "Save workspace image? [y/n/c]:", so we send "n".
            self.process.stdin.write(b"n\n")
            await self.process.stdin.drain()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
        self._stop_reading = True
        if self._reader_task:
            await self._reader_task

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


# Example usage demonstrating both send_command and directly calling read_until_marker.
async def main():
    # On Windows, adjust the executable name as needed (e.g., "R.exe" or "Rterm.exe").
    r = await AsyncRInterpreter.create(r_executable="R.exe")
    try:
        # Send a command that prints a message.
        output = await r.send_command("print('Hello from R!')", timeout=10)
        print("Output from send_command:")
        print(output)

        # Send another command.
        output = await r.send_command("sum(1:10)", timeout=10)
        print("Output from send_command:")
        print(output)

        # send a command with error
        output = await r.send_command("sum(1:10) + a", timeout=10)
        print("Output from send_command:")
        print(output)

        # send a variable
        output = await r.send_command("a <- 1", timeout=10)
        print("Output from send_command:")
        print(output)

        # send a variable
        output = await r.send_command("a", timeout=10)
        print("Output from send_command:")
        print(output)

        # send a multi-line command
        output = await r.send_command("""
        print('Hello from R!')
        print('Hello from R!')
        print('Hello from R!')
        """, timeout=10)
        print("Output from send_command:")
        print(output)

        # Alternatively, if you need to read the stdout directly (for commands that do not automatically inject the marker),
        # you can call read_until_marker after manually sending a marker.
        #
        # For demonstration, we send a print command and then inject the marker.
        r.process.stdin.write(b"print('Another message\\n')\n")

        r.process.stdin.write(b"cat('__COMMAND_FINISHED__\\n')\n")
        await r.process.stdin.drain()
        direct_output = await r.read_until_marker(timeout=10)
        print("Output from direct read_until_marker call:")
        print(direct_output)
    finally:
        await r.close()


if __name__ == "__main__":
    asyncio.run(main())
