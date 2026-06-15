import shlex
import typing as T
import subprocess as subp
from threading import Thread
from queue import Queue


class ProcessRunner(object):
    """Subprocess runner, allow stream stdout and stderr."""
    def __init__(self, command: str) -> None:
        self.command = command
        self.queue: Queue[T.Tuple[T.IO[bytes], bytes]] = Queue(0)
        self.proc: T.Optional[subp.Popen] = None
        self.t_stdout: T.Optional[Thread] = None
        self.t_stderr: T.Optional[Thread] = None

    def run(
            self,
            capture_stdout: bool = True,
            capture_stderr: bool = True,
            shell: bool = False,
            **kwargs: T.Any):
        """Run the command using subprocess.Popen.

        Args:
            capture_stdout: If True, capture stdout.
            capture_stderr: If True, capture stderr.
            shell: If True, run the command using the shell.
            **kwargs: other keyword arguments for subprocess.Popen
        """
        exe: T.Union[str, T.List[str]]
        if shell:
            exe = self.command
        else:
            exe = shlex.split(self.command)
        sout = subp.PIPE if capture_stdout else None
        serr = subp.PIPE if capture_stderr else None
        self.proc = subp.Popen(
            exe, stdout=sout, stderr=serr, shell=shell, **kwargs)
        if capture_stdout:
            self.t_stdout = Thread(
                target=self.reader_func,
                args=(self.proc.stdout, "stdout", self.queue))
            self.t_stdout.start()
        if capture_stderr:
            self.t_stderr = Thread(
                target=self.reader_func,
                args=(self.proc.stderr, "stderr", self.queue))
            self.t_stderr.start()

    @staticmethod
    def reader_func(pipe: T.IO[bytes], label: str, queue: "Queue"):
        """https://stackoverflow.com/a/31867499/8500469"""
        try:
            with pipe:
                for line in iter(pipe.readline, b''):
                    queue.put((label, line))
        finally:
            queue.put(None)

    def stream(self):
        """https://stackoverflow.com/a/31867499/8500469"""
        num_end_signals = 0
        if self.t_stdout is not None:
            num_end_signals += 1
        if self.t_stderr is not None:
            num_end_signals += 1
        for _ in range(num_end_signals):
            for src, line in iter(self.queue.get, None):
                line_decoded = line.decode()
                yield src, line_decoded
        return self.proc.wait()

    def write_stream_until_stop(
            self,
            out_file: T.TextIO,
            err_file: T.TextIO,
            flush_streams_each_time: bool = False,
            ) -> int:
        g = self.stream()
        retcode = None
        while True:
            try:
                src, line = next(g)
                if src == 'stdout':
                    ofile = out_file
                else:
                    assert src == 'stderr'
                    ofile = err_file
                ofile.write(line)
                if flush_streams_each_time:
                    ofile.flush()
            except StopIteration as e:
                retcode = e.value
                break
        return retcode
