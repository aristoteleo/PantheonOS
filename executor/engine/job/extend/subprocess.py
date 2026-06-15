import copy
import typing as T
import subprocess as subp
from pathlib import Path

from ..condition import Condition
from ..thread import ThreadJob
from ..base import Job

from cmd2func.runner import ProcessRunner


def SubprocessJob(
    cmd: str, record_cmd: bool = True,
    base_class: T.Type[Job] = ThreadJob,
    callback: T.Optional[T.Callable[[T.Any], None]] = None,
    error_callback: T.Optional[T.Callable[[Exception], None]] = None,
    retries: int = 0,
    retry_time_delta: float = 0.0,
    name: T.Optional[str] = None,
    condition: T.Optional[Condition] = None,
    wait_time_delta: float = 0.01,
    redirect_out_err: bool = False,
    target_dir: str = "$current_dir",
    popen_kwargs: T.Optional[T.Dict[str, T.Any]] = None,
    **attrs
):
    """Create a job that runs a subprocess.

    Args:
        cmd: The command to run.
        record_cmd: Whether to record the command to a file.
        base_class: The base class of the job.
        callback: The callback function.
        error_callback: The error callback function.
        retries: The number of retries.
        retry_time_delta: The time delta between retries.
        name: The name of the job.
        condition: The condition of the job.
        wait_time_delta: The time delta between each check.
        redirect_out_err: Whether to redirect stdout and stderr to files.
        target_dir: The target directory path for run the command.
            Use '$cache_dir' to represent the cache directory of the job.
            Use '$current_dir' to represent the current directory of the job.
            Default is '$current_dir'.
        popen_kwargs: The keyword arguments for subprocess.Popen.
        **attrs: Other attributes of the job.
    """
    class _SubprocessJob(base_class):  # type: ignore
        cmd: str
        target_dir: str

        repr_attrs = [
            ('status', lambda self: self.status),
            ('id', lambda self: self.id),
            ('cmd', lambda self: self.cmd),
            ('target_dir', lambda self: self.target_dir),
            ('base_class', lambda _: base_class.__name__),
            ("condition", lambda self: self.condition),
            ("retry_remain", lambda self: self.retry_remain),
        ]

        def __init__(self) -> None:
            self.cmd = cmd
            self.record_cmd = record_cmd
            nonlocal name
            if name is None:
                name = cmd.split()[0]
            self.target_dir = target_dir
            attrs.update({
                'cmd': cmd,
                'target_dir': target_dir,
            })
            super().__init__(
                lambda x: x,
                callback=callback,
                error_callback=error_callback,
                retries=retries,
                retry_time_delta=retry_time_delta,
                name=name,
                condition=condition,
                wait_time_delta=wait_time_delta,
                redirect_out_err=redirect_out_err,
                **attrs
            )
            self.runner: T.Optional[ProcessRunner] = None

        def resolve_target_dir(self, target_dir: str) -> str:
            if target_dir == "$cache_dir":
                return self.cache_dir.resolve().as_posix()
            elif target_dir == "$current_dir":
                return Path.cwd().resolve().as_posix()
            else:
                return Path(target_dir).resolve().as_posix()

        def process_func(self):
            cmd = copy.copy(self.cmd)
            record_cmd = copy.copy(self.record_cmd)
            target_dir = self.resolve_target_dir(self.target_dir)
            self.attrs.update({
                'target_dir': target_dir,
            })
            self.target_dir = target_dir

            cache_dir = self.cache_dir.resolve()
            path_sh = cache_dir / 'command.sh'

            def record_command():
                with open(path_sh, 'w') as f:
                    f.write(cmd + "\n")

            pkwargs = popen_kwargs or {}
            pkwargs['cwd'] = target_dir

            if self.redirect_out_err is not False:
                if isinstance(self.redirect_out_err, str):
                    path_stdout = Path(self.redirect_out_err)
                    if not path_stdout.parent.exists():
                        path_stdout.parent.mkdir(parents=True, exist_ok=True)
                    path_stdout = self.redirect_out_err
                    path_stderr = self.redirect_out_err
                else:
                    path_stdout = cache_dir / 'stdout.txt'
                    path_stderr = cache_dir / 'stderr.txt'

                def _run_cmd(runner: ProcessRunner):  # pragma: no cover
                    runner.run(**pkwargs)
                    if path_stdout == path_stderr:
                        fo = open(path_stdout, 'a')
                        fe = fo
                    else:
                        fo = open(path_stdout, 'a')
                        fe = open(path_stderr, 'a')
                    retcode = runner.write_stream_until_stop(
                        fo, fe, flush_streams_each_time=True)
                    fo.close()
                    if path_stdout != path_stderr:
                        fe.close()
                    return retcode
            else:
                def _run_cmd(runner: ProcessRunner):
                    runner.run(
                        capture_stdout=False,
                        capture_stderr=False,
                        **pkwargs
                    )
                    retcode = runner.proc.wait()
                    return retcode

            if base_class is ThreadJob:
                runner = ProcessRunner(cmd)
                self.runner = runner

                def run_cmd():
                    return _run_cmd(runner)
            else:
                def run_cmd():
                    runner = ProcessRunner(cmd)
                    return _run_cmd(runner)

            def func():
                if record_cmd:
                    record_command()
                retcode = run_cmd()
                if retcode > 0:
                    raise subp.SubprocessError(
                        f"Command '{cmd}' run failed, return code: {retcode}")
                return retcode
            self.func = func

        async def cancel(self):
            if self.runner is not None:
                self.runner.proc.terminate()
            await super().cancel()

    return _SubprocessJob()
