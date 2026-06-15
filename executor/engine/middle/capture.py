import sys
import typing as T
from functools import update_wrapper
from pathlib import Path
import traceback
import loguru


LOGURU_HANDLERS = {}


class Tee(object):
    def __init__(
            self, out_file: T.TextIO,
            stream: T.Literal['stdout', 'stderr'] = 'stdout'):
        self.stream_type = stream
        self.file = out_file
        self.stream: T.TextIO = getattr(sys, stream)

    def write(self, data):
        self.stream.write(data)
        self.file.write(data)

    def flush(self):
        self.stream.flush()
        self.file.flush()

    def __enter__(self):
        setattr(sys, self.stream_type, self)
        fname = repr(self.file)
        if fname not in LOGURU_HANDLERS:
            loguru_handler = loguru.logger.add(self.file)
            LOGURU_HANDLERS[fname] = loguru_handler
        return self

    def __exit__(self, _type, _value, _traceback):
        setattr(sys, self.stream_type, self.stream)
        fname = repr(self.file)
        if fname in LOGURU_HANDLERS:
            loguru.logger.remove(LOGURU_HANDLERS[fname])
            del LOGURU_HANDLERS[fname]


class CaptureOut(object):
    def __init__(
            self, func: T.Callable,
            stdout_file: Path, stderr_file: Path,
            capture_traceback: bool = True):
        update_wrapper(func, self)
        self.func = func
        self.stdout_file = stdout_file
        self.stderr_file = stderr_file
        self.capture_traceback = capture_traceback

    def __call__(self, *args, **kwargs) -> T.Any:
        if not self.stdout_file.parent.exists():
            self.stdout_file.parent.mkdir(
                parents=True, exist_ok=True)  # pragma: no cover
        if not self.stderr_file.parent.exists():
            self.stderr_file.parent.mkdir(
                parents=True, exist_ok=True)  # pragma: no cover

        if self.stdout_file == self.stderr_file:
            outf = open(self.stdout_file, 'a')
            errf = outf
        else:
            outf = open(self.stdout_file, 'a')
            errf = open(self.stderr_file, 'a')
        with Tee(outf, 'stdout'), Tee(errf, 'stderr'):
            try:
                res = self.func(*args, **kwargs)
            except Exception as e:
                if self.capture_traceback:
                    traceback.print_exc()
                raise e
            finally:
                outf.close()
                if self.stdout_file != self.stderr_file:
                    errf.close()
        return res
