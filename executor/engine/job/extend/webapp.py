import time
import typing as T
import copy
import sys

from loky.backend.process import LokyProcess
from cmd2func.runner import ProcessRunner

from ..process import ProcessJob
from ..base import Job
from ..condition import Condition
from ...utils import PortManager, ExecutorError


LauncherFunc = T.Callable[[str, int], None]


def WebappJob(
    web_launcher: T.Union[LauncherFunc, str],
    ip: str = "127.0.0.1", port: T.Optional[int] = None,
    base_class: T.Type[Job] = ProcessJob,
    check_times: int = 6,
    check_delta: float = 1.0,
    callback: T.Optional[T.Callable[[T.Any], None]] = None,
    error_callback: T.Optional[T.Callable[[Exception], None]] = None,
    retries: int = 0,
    retry_time_delta: float = 0.0,
    name: T.Optional[str] = None,
    condition: T.Optional[Condition] = None,
    wait_time_delta: float = 0.01,
    redirect_out_err: bool = False,
    **attrs
):
    """Create a job that runs a web app.

    Args:
        web_launcher: The function to launch the web app.
            The function should accept two arguments: ip and port.
        ip: The ip address of the web app.
        port: The port of the web app. If None, will find a free port.
        base_class: The base class of the job.
        check_times: The number of times to check the web app.
        check_delta: The time delta between each check.
        callback: The callback function.
        error_callback: The error callback function.
        retries: The number of retries.
        retry_time_delta: The time delta between retries.
        name: The name of the job.
        condition: The condition of the job.
        wait_time_delta: The time delta between each check.
        redirect_out_err: Whether to redirect stdout and stderr to files.
        **attrs: Other attributes of the job.
    """
    class _WebappJob(base_class):  # type: ignore
        ip: str
        port: T.Optional[int]

        repr_attrs = [
            ('status', lambda self: self.status),
            ('id', lambda self: self.id),
            ('addr', lambda self: f'{self.ip}:{self.port}'),
            ('base_class', lambda _: base_class.__name__),
            ("condition", lambda self: self.condition),
            ("retry_remain", lambda self: self.retry_remain),
        ]

        def __init__(self) -> None:
            self.ip = ip
            if ip not in ("127.0.0.1", "localhost", "0.0.0.0"):
                raise NotImplementedError(
                    "WebappJob now only support launch in local mechine.")
            self.port = port
            self.check_web_launcher(web_launcher)
            self.web_launcher = web_launcher
            self.check_times = check_times
            self.check_delta = check_delta
            if isinstance(web_launcher, str):
                attrs.update({"cmd": web_launcher})
            super().__init__(
                lambda x: x, callback=callback,
                error_callback=error_callback,
                retries=retries,
                retry_time_delta=retry_time_delta,
                name=name,
                condition=condition,
                wait_time_delta=wait_time_delta,
                redirect_out_err=redirect_out_err,
                **attrs
            )

        @staticmethod
        def check_web_launcher(web_launcher: T.Union[LauncherFunc, str]):
            if isinstance(web_launcher, str):
                cmd_temp = web_launcher
                if ('{ip}' not in cmd_temp) or ('{port}' not in cmd_temp):
                    raise ValueError(
                        "web_launcher should has ip and port placeholder.")
            else:
                if not callable(web_launcher):
                    raise TypeError(
                        "web_launcher should be a callable object or str.")

        def consume_resource(self) -> bool:
            if super().consume_resource():
                if self.port is None:
                    self.port = PortManager.get_port()
                else:
                    PortManager.consume_port(self.port)
                self.attrs.update({"address": f"{self.ip}:{self.port}"})
                return True
            else:  # pragma: no cover
                return False

        def release_resource(self) -> bool:
            if self.port is None:  # pragma: no cover
                return False
            if super().release_resource():
                PortManager.release_port(self.port)
                return True
            else:  # pragma: no cover
                return False

        def process_func(self):
            web_launcher = copy.copy(self.web_launcher)
            if self.port is None:  # pragma: no cover
                raise ExecutorError("Unreachable code.")
            ip, port = copy.copy(self.ip), copy.copy(self.port)
            check_times = copy.copy(self.check_times)
            check_delta = copy.copy(self.check_delta)

            def check_port(pid: int) -> bool:  # pragma: no cover
                for _ in range(check_times):
                    time.sleep(check_delta)
                    if PortManager.process_has_port(pid, ip, port):
                        return True
                    print(f"Process is not listen on {ip}:{port}. Try again.")
                return False

            if isinstance(web_launcher, str):
                cmd = web_launcher.format(ip=ip, port=port)

                def func():  # pragma: no cover
                    runner = ProcessRunner(cmd)
                    runner.run()
                    if check_port(runner.proc.pid):
                        retcode = runner.write_stream_until_stop(
                            sys.stdout, sys.stderr)
                        sys.exit(retcode)
                    else:
                        runner.proc.terminate()
                        raise IOError(f"Process is not listen on {ip}:{port}.")
            else:
                def func():  # pragma: no cover
                    proc = LokyProcess(target=web_launcher, args=(ip, port))
                    proc.start()
                    pid = proc.pid
                    if check_port(pid):
                        proc.join()
                    else:
                        proc.terminate()
                        raise IOError(f"Process is not listen on {ip}:{port}.")

            self.func = func
            super().process_func()
    return _WebappJob()
