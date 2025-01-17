import typing
from functools import update_wrapper
from inspect import signature

from . import introspection
from ._cached_property import cached_property


try:
    import typeguard
except ImportError:
    typeguard = None    # type: ignore[assignment]
try:
    import hypothesis
except ImportError:
    hypothesis = None   # type: ignore[assignment]
else:
    import hypothesis.strategies
    from hypothesis.internal.reflection import proxies


F = typing.Callable[..., None]
FuzzInputType = typing.Union[bytes, bytearray, memoryview, typing.BinaryIO]
FuzzType = typing.Callable[[FuzzInputType], typing.Optional[bytes]]
ArgsKwargsType = typing.Tuple[tuple, typing.Dict[str, typing.Any]]
EXAMPLE = object()


class TestCase(typing.NamedTuple):
    """A callable object, wrapper around a function that must be tested.

    When called, calls the wrapped function, suppresses expected exceptions,
    checks the type of the result, and returns it.
    """

    args: typing.Tuple[typing.Any, ...]
    """Positional arguments to be passed in the function"""

    kwargs: typing.Dict[str, typing.Any]
    """Keyword arguments to be passed in the function"""

    func: typing.Callable
    """The function which will be called when the test case is called"""

    exceptions: typing.Tuple[typing.Type[Exception], ...]
    """Exceptions that must be suppressed.
    """

    check_types: bool
    """Check that the result matches return type of the function.
    """

    def __call__(self) -> typing.Any:
        """Calls the given test case returning the called functions result on success or
        Raising an exception on error
        """
        __tracebackhide__ = True
        try:
            result = self.func(*self.args, **self.kwargs)
        except self.exceptions:
            return typing.NoReturn
        self._check_result(result)
        return result

    def _check_result(self, result: typing.Any) -> None:
        if not self.check_types:
            return
        memo = typeguard._CallMemo(
            func=self.func,
            args=self.args,
            kwargs=self.kwargs,
        )
        typeguard.check_argument_types(memo=memo)
        typeguard.check_return_type(result, memo=memo)


class cases:  # noqa: N
    """Generate test cases for the given function.
    """

    func: typing.Callable
    """the function to test. Should be type annotated."""

    count: int
    """how many test cases to generate, defaults to 50."""

    kwargs: typing.Dict[str, typing.Any]
    """keyword arguments to pass into the function."""

    check_types: bool
    """check that the result matches return type of the function. Enabled by default."""

    settings: 'hypothesis.settings'
    """Hypothesis settings to use instead of default ones."""

    seed: typing.Optional[int]
    """Random seed to use when generating test cases. Use it to make tests deterministic."""

    def __init__(
        self,
        func: typing.Callable, *,
        count: int = 50,
        kwargs: typing.Optional[typing.Dict[str, typing.Any]] = None,
        check_types: typing.Optional[bool] = None,
        settings: typing.Optional['hypothesis.settings'] = None,
        seed: typing.Optional[int] = None,
    ) -> None:
        """
        Create test cases generator.

        ```pycon
        >>> import deal
        >>> @deal.pre(lambda a, b: b != 0)
        ... def div(a: int, b: int) -> float:
        ...   return a / b
        ...
        >>> cases = deal.cases(div)
        >>>
        ```

        """
        if hypothesis is None:  # pragma: no cover
            raise ImportError('hypothesis is not installed')
        if check_types is True and typeguard is None:  # pragma: no cover
            raise ImportError('typeguard is not installed')
        if check_types is None:
            check_types = True

        self.func = func  # type: ignore
        self.count = count
        self.kwargs = kwargs or {}
        self.check_types = check_types
        self.settings = settings or self._default_settings
        self.seed = seed

    def __iter__(self) -> typing.Iterator[TestCase]:
        """Emits test cases.

        It can be helpful when you want to see what test cases are generated.
        The recommend way is to use `deal.cases` as a decorator instead.

        ```pycon
        >>> import deal
        >>> @deal.pre(lambda a, b: b != 0)
        ... def div(a: int, b: int) -> float:
        ...   return a / b
        ...
        >>> cases = iter(deal.cases(div))
        >>> next(cases)
        TestCase(args=(), kwargs=..., func=<function div ...>, exceptions=(), check_types=True)
        >>> for case in cases:
        ...   result = case()  # execute the test case
        >>>
        ```

        """
        cases: typing.List[TestCase] = []
        test = self(cases.append)
        test()
        yield from cases

    def __repr__(self) -> str:
        args = [
            getattr(self.func, '__name__', repr(self.func)),
            f'count={self.count}',
        ]
        if self.seed is not None:
            args.append(f'seed={self.seed}')
        if self.kwargs:
            args.append(f'kwargs={repr(self.kwargs)}')
        return 'deal.cases({})'.format(', '.join(args))

    def _make_case(self, *args, **kwargs) -> TestCase:
        """Make test case with the given arguments.
        """
        return TestCase(
            args=args,
            kwargs=kwargs,
            func=self.func,
            exceptions=self.exceptions,
            check_types=self.check_types,
        )

    @cached_property
    def _contracts(self) -> typing.Tuple[introspection.Contract, ...]:
        return tuple(introspection.get_contracts(self.func))

    @cached_property
    def _pres(self) -> typing.Tuple[introspection.Pre, ...]:
        """Returns pre-condition validators.

        It is used in the process of generating hypothesis strategies
        To let hypothesis more effectively avoid wrong input values.
        """
        validators = []
        for obj in self._contracts:
            if isinstance(obj, introspection.Pre):
                validators.append(obj)
        return tuple(validators)

    @cached_property
    def exceptions(self) -> typing.Tuple[typing.Type[Exception], ...]:
        """
        Returns exceptions that will be suppressed by individual test cases.
        The exceptions are extracted from `@deal.raises` of the tested function.
        """
        exceptions: list = []
        for obj in self._contracts:
            if isinstance(obj, introspection.Raises):
                exceptions.extend(obj.exceptions)
        return tuple(exceptions)

    @cached_property
    def strategy(self) -> 'hypothesis.strategies.SearchStrategy':
        """Hypothesis strategy that is used to generate test cases.
        """
        kwargs = self.kwargs.copy()
        for name, value in kwargs.items():
            if isinstance(value, hypothesis.strategies.SearchStrategy):
                continue
            kwargs[name] = hypothesis.strategies.just(value)

        def pass_along_variables(*args, **kwargs) -> ArgsKwargsType:
            return args, kwargs

        pass_along_variables.__signature__ = signature(self.func)    # type: ignore
        update_wrapper(wrapper=pass_along_variables, wrapped=self.func)
        return hypothesis.strategies.builds(pass_along_variables, **kwargs)

    @property
    def _default_settings(self) -> 'hypothesis.settings':
        return hypothesis.settings(
            database=None,
            max_examples=self.count,
            # avoid showing deal guts
            verbosity=hypothesis.Verbosity.quiet,
            # raise the original exception instead of a fake one
            report_multiple_bugs=False,
            # print how to reproduce the failure
            print_blob=True,
            # if too many cases rejected, it is deal to blame
            suppress_health_check=[hypothesis.HealthCheck.filter_too_much],
        )

    @typing.overload
    def __call__(self, test_func: F) -> F:
        """Wrap a function to turn it into a proper Hypothesis test.

        This is the recommend way to use `deal.cases`. It is powerful and extendable.

        ```python
        >>> import deal
        >>> @deal.pre(lambda a, b: b != 0)
        ... def div(a: int, b: int) -> float:
        ...   return a / b
        ...
        >>> @deal.cases(div)
        ... def test_div(case):
        ...   ...     # do something before
        ...   case()  # run the test case
        ...   ...     # do something after
        ...
        >>> test_div()  # run all test cases for `div`
        >>>
        ```

        """

    @typing.overload
    def __call__(self) -> None:
        """Generate and run tests for a function.

        This is the fastest way to generate tests for a function.

        ```python
        >>> import deal
        >>> @deal.pre(lambda a, b: b != 0)
        ... def div(a: int, b: int) -> float:
        ...   return a / b
        ...
        >>> test_div = deal.cases(div)
        >>> test_div()  # run the test
        ```

        """

    @typing.overload
    def __call__(self, buffer: FuzzInputType) -> typing.Optional[bytes]:
        """Use a function as a fuzzing target.

        This is a way to provide a random buffer for Hypothesis.
        It can be helpful for heavy testing of something really critical.

        ```python
        >>> import deal
        >>> @deal.pre(lambda a, b: b != 0)
        ... def div(a: int, b: int) -> float:
        ...   return a / b
        ...
        >>> import atheris
        >>> test_div = deal.cases(div)
        >>> atheris.Setup([], test_div)
        ...
        >>> atheris.Fuzz()
        ...
        ```

        """

    def __call__(self, target=None):
        """Allows deal.cases to be used as decorator, test function, or fuzzing target.
        """
        __tracebackhide__ = True
        if target is None:
            self._run()
            return None
        if callable(target):
            return self._wrap(target)
        return self._run.hypothesis.fuzz_one_input(target)  # type: ignore[attr-defined]

    # a hack to make the test discoverable by pytest
    @property
    def __func__(self) -> F:
        return self._run

    @cached_property
    def _run(self) -> F:
        return self._wrap(lambda case: case())

    def _wrap(self, test_func: F) -> F:
        # precache all contracts, so hypothesis won't explode
        # because of inconsistent execution time.
        introspection.init_all(test_func)

        def run_examples(args: tuple, kwargs: dict) -> None:
            case = self._make_case()
            for contract in self._contracts:
                if not isinstance(contract, introspection.Example):
                    continue
                case = case._replace(func=contract.validate)
                test_func(case, *args, **kwargs)

        def wrapper(case: ArgsKwargsType, *args, **kwargs) -> None:
            __tracebackhide__ = True
            ex = case
            if ex is EXAMPLE:
                run_examples(args, kwargs)
                return
            for validator in self._pres:
                try:
                    validator.validate(*ex[0], **ex[1])
                except validator.exception_type:
                    hypothesis.reject()
            case = self._make_case(*ex[0], **ex[1])
            test_func(case, *args, **kwargs)

        wrapper = self._impersonate(wrapper=wrapper, wrapped=test_func)
        wrapper = hypothesis.example(case=EXAMPLE)(wrapper)
        wrapper = hypothesis.given(case=self.strategy)(wrapper)
        wrapper = self.settings(wrapper)
        if self.seed is not None:
            wrapper = hypothesis.seed(self.seed)(wrapper)
        return wrapper

    @staticmethod
    def _impersonate(wrapper: F, wrapped: F) -> F:
        if not hasattr(wrapped, '__code__'):
            def wrapped(case) -> None:
                pass
        wrapper = proxies(wrapped)(wrapper)
        if wrapper.__name__ == '<lambda>':
            wrapper.__name__ = 'test_func'
        return wrapper
