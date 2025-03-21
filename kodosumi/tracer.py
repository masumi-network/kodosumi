import sys


def _stderr(tag, *args):
    sys.stderr.write(
        f"<x-{tag}>{' '.join([str(a) for a in args])}</x-{tag}>")
    sys.stderr.flush()


def markdown(*args):
    _stderr("markdown", *args)

def html(*args):
    _stderr("html", *args)

def text(*args):
    _stderr("text", *args)
    

