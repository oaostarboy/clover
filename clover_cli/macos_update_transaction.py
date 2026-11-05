"""One lazy source/build transaction shared by nested updater entrypoints.

Only the outer invocation owns cleanup. The normal build path finishes early,
so restoration is verified before success receipts or the restart phase.
"""
from contextvars import ContextVar
from functools import wraps
from pathlib import Path

from clover_cli.macos_update_lifecycle import LifecycleRefused

_current = ContextVar('macos_update_transaction', default=None)


class _Transaction:
    def __init__(self):
        self.root = None
        self.context = None
        self.operations = None
        self.failed = False

    def ensure(self, root):
        root = Path(root).resolve()
        if self.failed:
            raise LifecycleRefused('An earlier update transaction failed')
        if self.root is not None and self.root != root:
            raise LifecycleRefused('Nested update changed installation ownership')
        if self.context is not None:
            return self.operations
        from clover_cli.macos_update_launchd import darwin_workspace_window
        context = darwin_workspace_window(root)
        try:
            operations = context.__enter__()
        except BaseException:
            self.failed = True
            raise
        self.root = root
        self.context = context
        self.operations = operations
        return operations

    def finish(self):
        context, self.context = self.context, None
        if context is None:
            return None
        operations, self.operations = self.operations, None
        try:
            context.__exit__(None, None, None)
        except BaseException:
            self.failed = True
            raise
        return operations


def source_update_scope(function):
    """Join an existing invocation or restore its resources on every exit."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        if _current.get() is not None:
            return function(*args, **kwargs)
        transaction = _Transaction()
        token = _current.set(transaction)
        try:
            try:
                result = function(*args, **kwargs)
                if transaction.failed:
                    raise LifecycleRefused('An earlier update transaction failed')
                return result
            finally:
                transaction.finish()
        finally:
            _current.reset(token)
    return wrapped


def begin_source_mutation(root):
    transaction = _current.get()
    if transaction is None:
        raise LifecycleRefused('Source mutation has no owning update transaction')
    return transaction.ensure(root)


def finish_source_mutation():
    transaction = _current.get()
    if transaction is None:
        raise LifecycleRefused('Source mutation has no owning update transaction')
    return transaction.finish()
