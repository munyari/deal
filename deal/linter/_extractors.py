import ast
import builtins
from types import SimpleNamespace

import astroid


TOKENS = SimpleNamespace(
    ATTR=(ast.Attribute, astroid.Attribute),
    BIN_OP=(ast.BinOp, astroid.BinOp),
    CALL=(ast.Call, astroid.Call),
    EXPR=(ast.Expr, astroid.Expr),
    FOR=(ast.For, astroid.For),
    IF=(ast.If, astroid.If),
    RAISE=(ast.Raise, astroid.Raise),
    RETURN=(ast.Return, astroid.Return),
    TRY=(ast.Try, astroid.TryExcept, astroid.TryFinally),
    UNARY_OP=(ast.UnaryOp, astroid.UnaryOp),
    WITH=(ast.With, astroid.With),
)


class Token:
    def __init__(self, value, line: int, col: int):
        self.value = value
        self.line = line
        self.col = col

    @property
    def row(self):
        return self.line - 1


def _traverse(body):
    for expr in body:
        if isinstance(expr, TOKENS.EXPR):
            yield expr.value
            continue
        if isinstance(expr, TOKENS.IF + TOKENS.FOR):
            yield from _traverse(body=expr.body)
            yield from _traverse(body=expr.orelse)
            continue
        if isinstance(expr, TOKENS.TRY):
            if hasattr(expr, 'orelse'):
                yield from _traverse(body=expr.orelse)
            if hasattr(expr, 'finalbody'):
                yield from _traverse(body=expr.finalbody)
            continue
        if isinstance(expr, TOKENS.WITH):
            yield from _traverse(body=expr.body)
        yield expr


def get_name(expr):
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, astroid.Name):
        return expr.name
    if isinstance(expr, astroid.Attribute):
        return get_name(expr.expr) + '.' + expr.attrname
    if isinstance(expr, ast.Attribute):
        return get_name(expr.value) + '.' + expr.attr
    return None


def get_exceptions(body: list = None):
    for expr in _traverse(body):
        token_info = dict(line=expr.lineno, col=expr.col_offset)

        # explicit raise
        if isinstance(expr, TOKENS.RAISE):
            name = get_name(expr.exc)
            # raise instance
            if not name and isinstance(expr.exc, TOKENS.CALL):
                name = get_name(expr.exc.func)
                if not name or name[0].islower():
                    continue
            exc = getattr(builtins, name, name)
            yield Token(value=exc, **token_info)
            continue

        # division by zero
        if isinstance(expr, TOKENS.BIN_OP):
            if isinstance(expr.op, ast.Div) or expr.op == '/':
                if isinstance(expr.right, astroid.Const) and expr.right.value == 0:
                    yield Token(value=ZeroDivisionError, **token_info)
                    continue
                if isinstance(expr.right, ast.Num) and expr.right.n == 0:
                    yield Token(value=ZeroDivisionError, **token_info)
                    continue

        # exit()
        if isinstance(expr, TOKENS.CALL):
            name = get_name(expr.func)
            if name and name == 'exit':
                yield Token(value=SystemExit, **token_info)
                continue
            # sys.exit()
            if isinstance(expr.func, TOKENS.ATTR):
                name = get_name(expr.func)
                if name and name == 'sys.exit':
                    yield Token(value=SystemExit, **token_info)
                    continue


def get_returns(body: list = None):
    for expr in _traverse(body):
        token_info = dict(line=expr.lineno, col=expr.col_offset)
        if not isinstance(expr, TOKENS.RETURN):
            continue

        # any constant value in astroid
        if isinstance(expr.value, astroid.Const):
            yield Token(value=expr.value.value, **token_info)
            continue

        # string, binary string
        if isinstance(expr.value, (ast.Str, ast.Bytes)):
            yield Token(value=expr.value.s, **token_info)
            continue

        # True, False, None
        if isinstance(expr.value, ast.NameConstant):
            yield Token(value=expr.value.value, **token_info)
            continue

        # positive number
        if isinstance(expr.value, ast.Num):
            yield Token(value=expr.value.n, **token_info)
            continue

        # negative number
        if isinstance(expr.value, TOKENS.UNARY_OP):
            is_minus = isinstance(expr.value.op, ast.USub) or expr.value.op == '-'
            if is_minus:
                if isinstance(expr.value.operand, ast.Num):
                    yield Token(value=-expr.value.operand.n, **token_info)
                    continue
                if isinstance(expr.value.operand, astroid.Const):
                    yield Token(value=-expr.value.operand.value, **token_info)
                    continue

        # astroid inference
        if hasattr(expr.value, 'infer'):
            for value in expr.value.infer():
                if value is astroid.Uninferable:
                    continue
                if isinstance(value, astroid.Const):
                    yield Token(value=value.value, **token_info)


def get_prints(body: list):
    for expr in _traverse(body):
        token_info = dict(line=expr.lineno, col=expr.col_offset)
        if isinstance(expr, TOKENS.CALL):
            name = get_name(expr.func)
            if name in ('print', 'sys.stdout', 'sys.stderr'):
                yield Token(value=name, **token_info)
            if name in ('sys.stdout.write', 'sys.stderr.write'):
                yield Token(value=name[:-6], **token_info)
            if name == 'open':
                if _is_open_to_write(expr):
                    yield Token(value='open', **token_info)
            continue

        if isinstance(expr, TOKENS.WITH):
            for item in expr.items:
                if isinstance(item, ast.withitem):
                    item = item.context_expr
                else:
                    item = item[0]
                if not isinstance(item, TOKENS.CALL):
                    continue
                name = get_name(item.func)
                if name == 'open':
                    if _is_open_to_write(item):
                        yield Token(value='open', **token_info)


def _is_open_to_write(expr) -> bool:
    for arg in expr.args:
        if isinstance(arg, astroid.Const) and arg.value == 'w':
            return True
        if isinstance(arg, ast.Str) and 'w' in arg.s:
            return True

    if not expr.keywords:
        return False
    for arg in expr.keywords:
        if arg.arg != 'mode':
            continue
        if isinstance(arg.value, astroid.Const) and 'w' in arg.value.value:
            return True
        if isinstance(arg.value, ast.Str) and 'w' in arg.value.s:
            return True
    return False
