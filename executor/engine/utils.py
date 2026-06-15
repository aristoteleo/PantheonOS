import typing as T
import asyncio
import contextlib
import psutil
import socket


class ExecutorError(Exception):
    pass


class CheckError(ExecutorError):
    pass


class TypeCheckError(CheckError):
    pass


class RangeCheckError(CheckError):
    pass


class CheckAttrRange(object):
    valid_range: T.Iterable[T.Any] = []
    attr = "__"

    def __get__(self, obj, objtype=None):
        return getattr(obj, self.attr)

    def check(self, obj, value):
        if value not in self.valid_range:
            raise RangeCheckError(
                f"{obj}'s {type(self)} attr should be "
                f"one of: {self.valid_range}"
            )

    def __set__(self, obj, value):
        self.check(obj, value)
        setattr(obj, self.attr, value)


Checker = T.Callable[[T.Any], bool]


class CheckAttrType(object):
    valid_type: T.List[T.Union[Checker, type]] = []
    attr = "__"

    def __get__(self, obj, objtype=None):
        return getattr(obj, self.attr)

    def __set__(self, obj, value):
        check_passed = []
        for tp in self.valid_type:
            if isinstance(tp, type):
                passed = isinstance(value, tp)
            else:
                assert isinstance(tp, T.Callable)
                passed = tp(value)
            check_passed.append(passed)
        is_valid_type = any(check_passed)
        if not is_valid_type:
            raise TypeCheckError(
                f"{obj}'s {type(self)} attr should in type: "
                f"{self.valid_type}"
            )
        setattr(obj, self.attr, value)


@contextlib.contextmanager
def event_loop():
    loop, is_new_loop = get_event_loop()
    try:
        yield loop
    finally:
        if is_new_loop:
            loop.close()


def get_event_loop() -> T.Tuple[asyncio.AbstractEventLoop, bool]:
    is_new_loop = False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        is_new_loop = True
    return loop, is_new_loop


class PortManager():

    used_port: T.Set[int] = set()

    @classmethod
    def get_port(cls) -> int:
        while True:
            port = cls.find_free_port()
            if port in cls.used_port:  # pragma: no cover
                continue
            else:
                cls.consume_port(port)
                return port

    @classmethod
    def consume_port(cls, port: int):
        cls.used_port.add(port)

    @classmethod
    def release_port(cls, port: int):
        cls.used_port.remove(port)

    @staticmethod
    def find_free_port() -> int:
        """https://stackoverflow.com/a/45690594/8500469"""
        with contextlib.closing(
                socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.bind(('', 0))
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            port = s.getsockname()[1]
        return port

    @staticmethod
    def process_has_port(target_pid: int, ip: str, port: int) -> bool:
        p = psutil.Process(target_pid)
        addrs = [
            (c.laddr.ip, c.laddr.port) for c in p.connections()
        ]
        return (ip, port) in addrs


def get_callable_name(callable) -> str:
    if hasattr(callable, "func"):
        inner_func = getattr(callable, "func")
        return get_callable_name(inner_func)
    elif hasattr(callable, "__name__"):
        return getattr(callable, "__name__")
    elif hasattr(callable, "__class__"):
        return getattr(callable, "__class__").__name__
    else:  # pragma: no cover
        return str(callable)
