"""Cooperative, attempt-scoped control hooks used by all worker transports."""
class Cancelled(RuntimeError):pass


def check_cancel(callback):
    if callback and callback():raise Cancelled('Cancelled by the reviewer')
