import sys


def _stderr(tag, *args):
    sys.stderr.write(f"<x-{tag}>{' '.join(args)}</x-{tag}>\n")
    sys.stderr.flush()


def markdown(*args):
    _stderr("markdown", *args)

def html(*args):
    _stderr("html", *args)

def text(*args):
    _stderr("text", *args)
    

